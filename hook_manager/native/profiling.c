/* SPDX-License-Identifier: MPL-2.0 */
/* Collect function invocation counts and elapsed time for one race context.
 *
 * Reading order:
 *   initialize/start   reserve storage and enroll the calling Python thread;
 *   begin/leave        receive CPython entry, return and exception-unwind events;
 *   checkpoint        split open calls at a lap, pause or capture-end instant;
 *   boundary/pause/stop drive those splits without changing game execution;
 *   snapshot          build Python result objects after collection has stopped.
 *
 * A "function" here is one selected code object. A Frame represents one active
 * invocation, so recursion creates several Frames referring to the same function.
 * A Row aggregates that function's invocations/time within one window. The Python
 * controller maps zero-based window indices to numbered laps or finishing work.
 *
 * Only selected calls on the enrolled thread enter the stack. Unselected calls
 * have no Frame; their time remains inside the nearest selected caller's time.
 * Inclusive time contains measured children. Boundary-exclusive time subtracts
 * those children once; it is not full function self/CPU time.
 *
 * All callbacks run with the GIL held. Their own code allocates no Python objects
 * and reports failure in native state rather than raising into the game. Python
 * owns the monitoring-tool lease, callback registration and teardown; this file
 * owns counters and timing only. See profiling_collector.py for that lifecycle.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <stdlib.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <time.h>
#endif

#define MAX_FUNCTIONS 256
#define MAX_WINDOWS 512
#define MAX_DEPTH 1024
/* Power of two, with at most 256 occupied slots: lookup always finds an empty
 * slot and needs no resizing/allocation during an invocation. */
#define HASH_SIZE 1024
/* CPython co_flags: CO_GENERATOR, CO_COROUTINE, CO_ASYNC_GENERATOR. These need
 * suspension/task accounting beyond this synchronous measured-call stack. */
#define RESUMABLE_FLAGS (0x20 | 0x80 | 0x200)

/* Row layout, also the counter order exported by snapshot(). Counts describe
 * invocations, including separate recursive invocations of the same function.
 * For a complete row: INCOMING + STARTED = COMPLETED + TRUNCATED + OUTGOING. */
enum {
    STARTED,      /* Entries first observed in this window. */
    COMPLETED,    /* Observed returns or exception unwinds in this window. */
    INCOMING,     /* Open calls carried from the preceding window. */
    OUTGOING,     /* Open calls carried into the next window. */
    TRUNCATED,    /* Calls still open when reporting stops, without an exit. */
    EXCEPTIONAL,  /* Subset of COMPLETED that exited by exception unwinding. */
    INCLUSIVE,    /* Active elapsed nanoseconds, including measured children. */
    EXCLUSIVE     /* Inclusive minus measured synchronous child time. */
};
/* failure indexes reasons[]; OK is the initial zero-filled state. */
enum { OK, CAPACITY, COUNTER, CLOCK, MISMATCH };
static const char *reasons[] = {
    "", "collector_overflow", "counter_overflow", "clock_error", "event_mismatch"
};

typedef struct { uint64_t value[8]; } Row;
typedef struct {
    int function;       /* Index into codes[], not a Python callable/address. */
    uint64_t start,     /* Entry or latest checkpoint time for this open slice. */
             child;     /* Completed measured-child slices since that start. */
} Frame;
typedef struct {
    uint64_t start,     /* Absolute monotonic start of the whole window. */
             wall,      /* Full window duration, including pause; set on close. */
             paused;    /* Closed pause segments accumulated in this window. */
} Window;
typedef struct {
    PyObject_HEAD
    /* Strong references keep selected code identities alive until release().
     * slots[] maps a hashed code pointer to function index + 1; zero means empty. */
    PyObject *codes[MAX_FUNCTIONS];
    int slots[HASH_SIZE],
        functions,      /* Number of admitted code objects / columns per window. */
        capacity,       /* Reserved window count, including finishing if used. */
        maximum_depth;  /* Reserved active measured-invocation count. */
    int window,         /* Current zero-based index into windows[] / row matrix. */
        depth,          /* frames[0..depth) is the measured root-to-leaf stack. */
        configured,     /* __init__ attempted; reject reinitialization. */
        started,        /* start() succeeded once; this object is one-shot. */
        stopped,        /* stop() finalized reporting; snapshot() is now allowed. */
        active,         /* Event accumulation enabled; failure clears this early. */
        paused,         /* Keep counting calls, but accrue zero elapsed time. */
        failure;        /* First failure reason, retained through stop/export. */
    uint64_t owner;     /* Unique CPython thread-state ID, not a reusable OS ID. */
    uint64_t last_timestamp; /* Latest accepted read, including gaps between roots. */
    uint64_t counter_limit, /* UINT64_MAX normally; lower caps exercise failures. */
             callbacks,     /* Accepted entry/exit events on the enrolled thread. */
             ignored,       /* Selected entry/exit events on other threads. */
             pause_start;   /* Start of the currently uncharged pause segment. */
    Row *rows;          /* Flat [reserved window][function] zero-initialized matrix. */
    Frame *frames;      /* Preallocated invocation stack; no per-entry malloc. */
    Window *windows;    /* All reserved clocks; export only the observed prefix. */
} Collector;

#ifdef _WIN32
static uint64_t frequency;
#define CLOCK_KIND "QueryPerformanceCounter"
#else
#define CLOCK_KIND "CLOCK_MONOTONIC"
#endif
static uint64_t resolution_ns;

/* A valid OS timestamp can still exceed this collector's uint64 nanosecond
 * representation. Check both whole seconds and the final fraction before adding
 * them, so clock conversion never silently wraps to an earlier timestamp. */
static int timestamp_ns(uint64_t seconds, uint64_t fraction, uint64_t *result) {
    uint64_t whole;
    if (seconds > UINT64_MAX / 1000000000ULL) return 0;
    whole = seconds * 1000000000ULL;
    if (fraction > UINT64_MAX - whole) return 0;
    *result = whole + fraction;
    return 1;
}

/* Read an absolute monotonic timestamp. Callers subtract timestamps to form
 * durations; a nanosecond representation does not imply nanosecond accuracy. */
static int clock_ns(uint64_t *result) {
#ifdef _WIN32
    LARGE_INTEGER ticks;
    if (!QueryPerformanceCounter(&ticks) || ticks.QuadPart < 0) return 0;
    /* Convert whole seconds and the remainder separately, avoiding a multiply
     * of the entire uptime tick count by one billion. Init validates frequency. */
    return timestamp_ns((uint64_t)ticks.QuadPart / frequency,
        ((uint64_t)ticks.QuadPart % frequency) * 1000000000ULL / frequency, result);
#else
    struct timespec ticks;
    if (clock_gettime(CLOCK_MONOTONIC, &ticks) || ticks.tv_sec < 0) return 0;
    return timestamp_ns((uint64_t)ticks.tv_sec, (uint64_t)ticks.tv_nsec, result);
#endif
}

static void fail(Collector *self, int reason) {
    /* Writes before a failure can remain partially applied. Preserve the first
     * cause and stop further events; export must label the data incomplete. */
    if (!self->failure) self->failure = reason;
    self->active = 0;
}

static int add(Collector *self, uint64_t *value, uint64_t amount) {
    /* Check before addition so unsigned wraparound cannot hide an overflow.
     * Stored counters and completed-child accumulators stay within the limit. */
    if (amount > self->counter_limit - *value) {
        fail(self, COUNTER);
        return 0;
    }
    *value += amount;
    return 1;
}

static int now(Collector *self, uint64_t *value) {
    /* Check the whole observed clock stream, including time between completed
     * root calls. Frame-only checks cannot detect a reversal in an empty stack. */
    if (!clock_ns(value) || *value < self->last_timestamp) {
        fail(self, CLOCK);
        return 0;
    }
    self->last_timestamp = *value;
    return 1;
}

static Row *row(Collector *self, int function) {
    /* Rows are window-major: adjacent function indices share one lap/window. */
    return &self->rows[self->window * self->functions + function];
}

static int lookup(Collector *self, PyObject *code) {
    /* Pointer identity distinguishes separate, structurally equal code objects.
     * Drop low alignment bits, then linearly probe collisions. No Python hashing,
     * equality, qualified-name lookup or author code runs on this path. */
    unsigned int at = (unsigned int)(((uintptr_t)code >> 4) & (HASH_SIZE - 1));
    while (self->slots[at]) {
        int function = self->slots[at] - 1;
        if (self->codes[function] == code) return function;
        at = (at + 1) & (HASH_SIZE - 1);
    }
    return -1;
}

/* Commit the open slice of every measured invocation at one shared instant.
 *
 * For each frame:
 *   inclusive slice = timestamp - frame.start (zero while paused)
 *   child slices    = completed children + current open child's inclusive slice
 *   exclusive slice = inclusive slice - child slices
 * Example: 100 ns elapsed, 20 ns completed child, 30 ns open child => 50 ns
 * exclusive. Grandchildren are already contained in their parent's child slice;
 * subtracting them again here would double-count their exclusion.
 *
 * Visit root to leaf: a parent needs its child's OLD start before that child is
 * reset. Then move every start to timestamp and clear completed-child time, so
 * the next return/checkpoint charges only new elapsed time. Stack entries remain
 * open and invocation counts are unchanged. This serves lap, pause and stop.
 */
static int checkpoint(Collector *self, uint64_t timestamp) {
    int index;
    for (index = 0; index < self->depth; ++index) {
        Frame *frame = &self->frames[index];
        uint64_t elapsed, child = frame->child;
        if (timestamp < frame->start) { fail(self, CLOCK); return 0; }
        elapsed = self->paused ? 0 : timestamp - frame->start;
        if (!self->paused && index + 1 < self->depth) {
            uint64_t start = self->frames[index + 1].start;
            if (timestamp < start) { fail(self, CLOCK); return 0; }
            if (!add(self, &child, timestamp - start)) return 0;
        }
        if (elapsed < child) { fail(self, CLOCK); return 0; }
        if (!add(self, &row(self, frame->function)->value[INCLUSIVE], elapsed) ||
            !add(self, &row(self, frame->function)->value[EXCLUSIVE], elapsed - child)) return 0;
        frame->start = timestamp;
        frame->child = 0;
    }
    return 1;
}

static int close_window(Collector *self, uint64_t timestamp) {
    Window *window = &self->windows[self->window];
    if (!checkpoint(self, timestamp)) return 0;
    window->wall = timestamp - window->start;
    if (window->wall > self->counter_limit) { fail(self, COUNTER); return 0; }
    if (self->paused) {
        /* A pause can span windows. Charge only the segment in the closing
         * window, and restart its accounting at this same boundary instant. */
        if (!add(self, &window->paused, timestamp - self->pause_start)) return 0;
        self->pause_start = timestamp;
    }
    return 1;
}

static int control(Collector *self) {
    /* Explicit lifecycle calls may raise for misuse. Notification callbacks use
     * fail()/None instead, preserving the game call's own return or exception. */
    if (!self->started || self->stopped || PyThreadState_GetID(PyThreadState_Get()) != self->owner) {
        PyErr_SetString(PyExc_RuntimeError, "Control capture on its enrolled thread while running");
        return 0;
    }
    return 1;
}

static void release(Collector *self) {
    /* Also handles partial admission: functions counts exactly the references
     * acquired so far. Monitoring registration belongs to the Python controller. */
    int index;
    for (index = 0; index < self->functions; ++index) Py_CLEAR(self->codes[index]);
    self->functions = 0;
    free(self->rows); self->rows = NULL;
    free(self->frames); self->frames = NULL;
    free(self->windows); self->windows = NULL;
}

static void touch_pages(void *memory, size_t bytes) {
    volatile unsigned char *pages = memory;
    size_t offset;
    /* calloc can leave demand-zero pages untouched. Fault them in during
     * preparation rather than charging their first write to measured calls.
     * Volatile keeps these zero writes even though calloc already zeroed memory. */
    for (offset = 0; offset < bytes; offset += 4096) pages[offset] = 0;
}

/* Collector(codes, windows=64, stack_depth=128, counter_limit=UINT64_MAX).
 * GenericNew supplies zeroed object fields. Validate and retain the code tuple,
 * build the identity table, then reserve all storage before start can activate.
 * Native validation also protects direct callers of this internal extension. */
static int initialize(Collector *self, PyObject *args, PyObject *keywords) {
    PyObject *codes, *types, *code_type, *counter_limit = NULL;
    int capacity = 64, maximum_depth = 128, index;
    unsigned long long limit = UINT64_MAX;
    static char *names[] = {"codes", "windows", "stack_depth", "counter_limit", NULL};
    if (self->configured) { PyErr_SetString(PyExc_RuntimeError, "Collector already configured"); return -1; }
    self->configured = 1;
    if (!PyArg_ParseTupleAndKeywords(args, keywords, "O|iiO", names,
                                    &codes, &capacity, &maximum_depth, &counter_limit)) return -1;
    if (counter_limit) {
        if (!PyLong_CheckExact(counter_limit)) {
            PyErr_SetString(PyExc_ValueError, "Expected an unsigned counter limit"); return -1;
        }
        limit = PyLong_AsUnsignedLongLong(counter_limit);
        if (PyErr_Occurred()) return -1;
    }
    if (!PyTuple_CheckExact(codes) || PyTuple_Size(codes) < 1 || PyTuple_Size(codes) > MAX_FUNCTIONS ||
        capacity < 1 || capacity > MAX_WINDOWS || maximum_depth < 1 || maximum_depth > MAX_DEPTH || !limit) {
        PyErr_SetString(PyExc_ValueError, "Expected 1..256 codes, 1..512 windows, 1..1024 stack depth and positive counter limit");
        return -1;
    }
    types = PyImport_ImportModule("types");
    /* Fetch CodeType through Python to keep this extension on the stable ABI,
     * without accessing CPython's private code-object layout. */
    if (!types) return -1;
    code_type = PyObject_GetAttrString(types, "CodeType");
    Py_DECREF(types);
    if (!code_type) return -1;
    for (index = 0; index < PyTuple_Size(codes); ++index) {
        PyObject *code = PyTuple_GetItem(codes, index), *flags;
        long value;
        unsigned int at;
        if ((PyObject *)Py_TYPE(code) != code_type || lookup(self, code) >= 0) {
            PyErr_SetString(PyExc_ValueError, "Expected unique CPython code objects");
            break;
        }
        flags = PyObject_GetAttrString(code, "co_flags");
        if (!flags) break;
        value = PyLong_AsLong(flags);
        Py_DECREF(flags);
        if (PyErr_Occurred()) break;
        /* CO_NEWLOCALS (0x02) excludes module/class bodies; resumable functions
         * require a different stack model and are rejected before activation. */
        if (!(value & 0x02) || value & RESUMABLE_FLAGS) {
            PyErr_SetString(PyExc_ValueError, "Only synchronous function code is supported");
            break;
        }
        Py_INCREF(code);
        self->codes[index] = code;
        ++self->functions;
        at = (unsigned int)(((uintptr_t)code >> 4) & (HASH_SIZE - 1));
        while (self->slots[at]) at = (at + 1) & (HASH_SIZE - 1);
        self->slots[at] = index + 1;
    }
    Py_DECREF(code_type);
    if (PyErr_Occurred()) { release(self); return -1; }
    self->rows = calloc((size_t)self->functions * capacity, sizeof(Row));
    self->frames = calloc((size_t)maximum_depth, sizeof(Frame));
    self->windows = calloc((size_t)capacity, sizeof(Window));
    if (!self->rows || !self->frames || !self->windows) {
        release(self); PyErr_NoMemory(); return -1;
    }
    touch_pages(self->rows, (size_t)self->functions * capacity * sizeof(Row));
    touch_pages(self->frames, (size_t)maximum_depth * sizeof(Frame));
    touch_pages(self->windows, (size_t)capacity * sizeof(Window));
    self->capacity = capacity;
    self->maximum_depth = maximum_depth;
    self->counter_limit = limit;
    return 0;
}

static void destroy(Collector *self) {
    PyTypeObject *type = Py_TYPE((PyObject *)self);
    freefunc free_object = (freefunc)PyType_GetSlot(type, Py_tp_free);
    release(self);
    /* This is a heap type created with PyType_FromSpec. Free the instance using
     * its allocation slot, then release the instance's reference to the type. */
    free_object((PyObject *)self);
    Py_DECREF(type);
}

static PyObject *start(Collector *self, PyObject *unused) {
    uint64_t timestamp;
    if (!self->rows || self->started) {
        PyErr_SetString(PyExc_RuntimeError, "A collector can start only once"); return NULL;
    }
    if (!clock_ns(&timestamp)) { PyErr_SetString(PyExc_RuntimeError, "Monotonic clock unavailable"); return NULL; }
    /* OS thread identifiers can be recycled after exit. Thread-state IDs keep
     * a later worker from silently inheriting this capture's context. */
    self->owner = PyThreadState_GetID(PyThreadState_Get());
    self->last_timestamp = timestamp;
    /* The first window is index zero. Python gives it the public label "lap 1"
     * and checks for already-active selected calls before enabling monitoring. */
    self->windows[0].start = timestamp;
    self->started = self->active = 1;
    Py_RETURN_NONE;
}

/* Common callback admission. CPython passes (code, instruction_offset) at entry
 * and adds the return value/exception at exit. We need only code identity.
 * Return its function index, or -1 to ignore the event / stop on a recorded fault.
 * Foreign-thread calls increment ignored, never the enrolled thread's stack. */
static int event(Collector *self, PyObject *const *args, Py_ssize_t count, int expected) {
    int function;
    if (!self->active) return -1;
    if (count != expected) { fail(self, MISMATCH); return -1; }
    function = lookup(self, args[0]);
    if (function < 0) return -1; /* PY_UNWIND is globally enabled. */
    if (PyThreadState_GetID(PyThreadState_Get()) != self->owner) {
        add(self, &self->ignored, 1); return -1;
    }
    if (!add(self, &self->callbacks, 1)) return -1;
    return function;
}

static PyObject *begin(Collector *self, PyObject *const *args, Py_ssize_t count) {
    uint64_t timestamp;
    int function = event(self, args, count, 2);
    Frame *frame;
    if (function < 0) Py_RETURN_NONE;
    if (self->depth == self->maximum_depth) { fail(self, CAPACITY); Py_RETURN_NONE; }
    if (!now(self, &timestamp) || !add(self, &row(self, function)->value[STARTED], 1)) Py_RETURN_NONE;
    /* Each recursive entry gets its own frame, even though all those frames
     * contribute to the same function's aggregate row. */
    frame = &self->frames[self->depth++];
    frame->function = function;
    frame->start = timestamp;
    frame->child = 0;
    Py_RETURN_NONE;
}

/* Shared normal-return and exception-unwind accounting. Only the slice since
 * entry/latest checkpoint is still uncharged. The completed invocation's slice
 * then becomes completed-child time in its nearest measured parent's frame. */
static PyObject *leave(Collector *self, PyObject *const *args, Py_ssize_t count, int exceptional) {
    uint64_t timestamp, elapsed;
    int function = event(self, args, count, 3);
    Frame *frame;
    Row *result;
    if (function < 0) Py_RETURN_NONE;
    if (!self->depth || self->frames[self->depth - 1].function != function) {
        /* A missing or reordered measured event invalidates the stack. Guessing
         * which call exited would assign elapsed time to the wrong function. */
        fail(self, MISMATCH); Py_RETURN_NONE;
    }
    frame = &self->frames[self->depth - 1];
    if (!now(self, &timestamp)) Py_RETURN_NONE;
    if (timestamp < frame->start) { fail(self, CLOCK); Py_RETURN_NONE; }
    elapsed = self->paused ? 0 : timestamp - frame->start;
    if (elapsed < frame->child) { fail(self, CLOCK); Py_RETURN_NONE; }
    result = row(self, function);
    /* Exceptional exits are also completions, not another invocation. The game
     * exception/return value in args[2] is left untouched. */
    if (!add(self, &result->value[COMPLETED], 1) ||
        !add(self, &result->value[EXCEPTIONAL], exceptional) ||
        !add(self, &result->value[INCLUSIVE], elapsed) ||
        !add(self, &result->value[EXCLUSIVE], elapsed - frame->child)) Py_RETURN_NONE;
    --self->depth;
    /* Propagate inclusive child time once: it already contains any measured
     * grandchildren. The parent's eventual exclusive time subtracts this total. */
    if (self->depth) add(self, &self->frames[self->depth - 1].child, elapsed);
    Py_RETURN_NONE;
}

static PyObject *end(Collector *self, PyObject *const *args, Py_ssize_t count) {
    return leave(self, args, count, 0);
}
static PyObject *unwind(Collector *self, PyObject *const *args, Py_ssize_t count) {
    return leave(self, args, count, 1);
}

static PyObject *boundary(Collector *self, PyObject *unused) {
    uint64_t timestamp;
    int index;
    if (!control(self)) return NULL;
    if (!self->active) Py_RETURN_FALSE;
    if (self->window + 1 == self->capacity) { fail(self, CAPACITY); Py_RETURN_FALSE; }
    if (!now(self, &timestamp) || !close_window(self, timestamp)) Py_RETURN_FALSE;
    /* Close the old row's invocation balance, switch the window, then open the
     * new row's balance for the SAME stack. No STARTED/COMPLETED counts are added.
     * Recursive frames each carry once; their shared function row sums them. */
    for (index = 0; index < self->depth; ++index)
        if (!add(self, &row(self, self->frames[index].function)->value[OUTGOING], 1)) Py_RETURN_FALSE;
    ++self->window;
    self->windows[self->window].start = timestamp;
    for (index = 0; index < self->depth; ++index)
        if (!add(self, &row(self, self->frames[index].function)->value[INCOMING], 1)) Py_RETURN_FALSE;
    Py_RETURN_TRUE;
}

static PyObject *pause_capture(Collector *self, PyObject *value) {
    uint64_t timestamp;
    int paused;
    if (!control(self)) return NULL;
    if (value != Py_True && value != Py_False) { PyErr_SetString(PyExc_ValueError, "Expected a pause boolean"); return NULL; }
    if (!self->active) Py_RETURN_NONE;
    paused = value == Py_True;
    if (paused == self->paused) Py_RETURN_NONE;
    /* Checkpoint under the OLD pause state: pausing charges the active prefix;
     * resuming charges zero paused function time, then restarts each open slice.
     * The wall-clock paused interval is recorded separately in the window. */
    if (!now(self, &timestamp) || !checkpoint(self, timestamp)) Py_RETURN_NONE;
    if (self->paused && !add(self, &self->windows[self->window].paused, timestamp - self->pause_start)) Py_RETURN_NONE;
    self->paused = paused;
    self->pause_start = timestamp;
    Py_RETURN_NONE;
}

static PyObject *stop(Collector *self, PyObject *unused) {
    int index;
    uint64_t timestamp;
    if (self->stopped) Py_RETURN_NONE;
    if (!control(self)) return NULL;
    /* Once failed, timing may have gaps/partial writes: do not manufacture a
     * final elapsed slice. Still truncate known open calls in reporting. */
    if (self->active && now(self, &timestamp)) close_window(self, timestamp);
    for (index = 0; index < self->depth; ++index)
        add(self, &row(self, self->frames[index].function)->value[TRUNCATED], 1);
    /* Drop our reporting stack only. Actual game frames keep executing; later
     * monitoring events see active == 0 until Python removes the callbacks. */
    self->depth = 0;
    self->active = 0;
    self->stopped = 1;
    Py_RETURN_NONE;
}

/* Post-stop export is deliberately outside callback execution. Python allocation
 * and its normal error handling are allowed here. The full bounded matrix is
 * materialized sparsely; this is internal output, not the eventual paged wire API.
 * allocated_bytes counts native storage only, excluding code refs/Python output. */
static PyObject *snapshot(Collector *self, PyObject *unused) {
    PyObject *rows, *windows;
    int window, function, field;
    uint64_t allocated = sizeof(*self) + (uint64_t)self->functions * self->capacity * sizeof(Row) +
        (uint64_t)self->maximum_depth * sizeof(Frame) + (uint64_t)self->capacity * sizeof(Window);
    if (!self->stopped) { PyErr_SetString(PyExc_RuntimeError, "Stop collection before exporting"); return NULL; }
    rows = PyList_New(0);
    windows = PyList_New(0);
    if (!rows || !windows) { Py_XDECREF(rows); Py_XDECREF(windows); return NULL; }
    for (window = 0; window <= self->window; ++window) {
        /* Emit every observed window even when all its function rows are zero.
         * "i" packs an index; "K" packs a full unsigned 64-bit Python integer. */
        Window *timing = &self->windows[window];
        PyObject *record = Py_BuildValue("(iKKK)", window, timing->start, timing->wall, timing->paused);
        if (!record || PyList_Append(windows, record) < 0) { Py_XDECREF(record); goto error; }
        Py_DECREF(record);
        for (function = 0; function < self->functions; ++function) {
            Row *value = &self->rows[window * self->functions + function];
            int present = 0;
            for (field = 0; field < 8; ++field) present |= value->value[field] != 0;
            if (!present) continue;
            /* Tuple: window index, function index, then the eight Row counters
             * in enum order above. Python labels the windows after export. */
            record = Py_BuildValue("(iiKKKKKKKK)", window, function,
                value->value[0], value->value[1], value->value[2], value->value[3],
                value->value[4], value->value[5], value->value[6], value->value[7]);
            if (!record || PyList_Append(rows, record) < 0) { Py_XDECREF(record); goto error; }
            Py_DECREF(record);
        }
    }
    /* "N" transfers each list's owned reference to the result dictionary.
     * PyList_Append above retains records, so their local refs were released. */
    return Py_BuildValue("{s:N,s:N,s:s,s:K,s:K,s:K,s:s,s:K,s:K}",
        "rows", rows, "windows", windows, "failure", reasons[self->failure],
        "callbacks", self->callbacks, "ignored_thread_events", self->ignored, "allocated_bytes", allocated,
        "clock", CLOCK_KIND, "clock_resolution_ns", resolution_ns, "thread_state_id", self->owner);
error:
    Py_DECREF(rows); Py_DECREF(windows); return NULL;
}

/* Bind the three event callbacks directly to this native instance. FASTCALL
 * receives CPython's argument array without creating a callback argument tuple.
 * Other methods are explicit preparation/boundary/export operations. */
static PyMethodDef methods[] = {
    {"start", (PyCFunction)start, METH_NOARGS, "Begin a one-shot enrolled-thread capture."},
    {"begin", (PyCFunction)begin, METH_FASTCALL, "Native PY_START callback."},
    {"end", (PyCFunction)end, METH_FASTCALL, "Native PY_RETURN callback."},
    {"unwind", (PyCFunction)unwind, METH_FASTCALL, "Native PY_UNWIND callback."},
    {"boundary", (PyCFunction)boundary, METH_NOARGS, "Checkpoint active calls and open the next window."},
    {"pause", (PyCFunction)pause_capture, METH_O, "Suspend or resume elapsed timing."},
    {"stop", (PyCFunction)stop, METH_NOARGS, "Checkpoint and truncate open calls."},
    {"snapshot", (PyCFunction)snapshot, METH_NOARGS, "Export stopped counters and window clocks."},
    {NULL, NULL, 0, NULL}
};
/* Stable-ABI heap type: every Collector has its own references, arenas and state.
 * The module shares only clock configuration, never a capture or its counters. */
static PyType_Slot slots[] = {
    {Py_tp_new, PyType_GenericNew}, {Py_tp_init, initialize},
    {Py_tp_dealloc, destroy}, {Py_tp_methods, methods}, {0, NULL}
};
static PyType_Spec specification = {"_race_profile.Collector", sizeof(Collector), 0, Py_TPFLAGS_DEFAULT, slots};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "_race_profile", "Internal bounded race counters.", 0, NULL};

PyMODINIT_FUNC PyInit__race_profile(void) {
    PyObject *result, *type;
#ifdef _WIN32
    LARGE_INTEGER ticks;
    /* Read clock frequency once, and bound the remainder conversion multiply
     * used by clock_ns(). Report the nominal tick resolution rounded up to ns. */
    if (!QueryPerformanceFrequency(&ticks) || ticks.QuadPart <= 0 ||
        (uint64_t)ticks.QuadPart > UINT64_MAX / 1000000000ULL) {
        PyErr_SetString(PyExc_RuntimeError, "Unsupported performance-counter frequency"); return NULL;
    }
    frequency = (uint64_t)ticks.QuadPart;
    resolution_ns = (1000000000ULL + frequency - 1) / frequency;
#else
    struct timespec resolution;
    if (clock_getres(CLOCK_MONOTONIC, &resolution)) {
        PyErr_SetString(PyExc_RuntimeError, "Monotonic clock resolution unavailable"); return NULL;
    }
    resolution_ns = (uint64_t)resolution.tv_sec * 1000000000ULL + resolution.tv_nsec;
    if (!resolution_ns) resolution_ns = 1;
#endif
    result = PyModule_Create(&module);
    if (!result) return NULL;
    type = PyType_FromSpec(&specification);
    if (!type || PyModule_AddObject(result, "Collector", type) < 0) {
        Py_XDECREF(type); Py_DECREF(result); return NULL;
    }
    return result;
}
