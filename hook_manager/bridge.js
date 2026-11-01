// SPDX-License-Identifier: MPL-2.0
// Trusted transport: run our fixed manager source at a Python main-thread GIL boundary.
const config = __HOOK_CONFIG__;
let observer = null;
let listener = null;
let busy = false;
let ready = false;
let stopped = false;
let failed = null;
let pending = null;
let armEntry = null;
let mainThread = null;

function detachEntry() {
    if (listener) { listener.detach(); listener = null; }
}

function detachTransport() {
    // No callback pointers are queued in Python, so these hooks can be detached on unload.
    detachEntry();
    if (observer) { observer.detach(); observer = null; }
}

function fail(error) {
    failed = error;
    send({ event: 'error', detail: String(error), traceback: error.traceback || error.stack, activity: error.activity });
    if (pending) { pending.reject(error); pending = null; }
    setImmediate(detachTransport);
}

function enqueue(operation, id, buttons, loading, settings, themes, regulations, prerequisites) {
    // The RPC thread only queues data; it must never call Python's API itself.
    if (!['status', 'changes', 'shutdown', 'menu', 'loading', 'settings', 'themes', 'prerequisites', 'adapters', 'regulation-defaults', 'await-regulations', 'prepare-regulations', 'install-regulations', 'activate-regulations', 'stop-regulations', 'retire-regulations', 'restore-native-rules'].includes(operation)) throw new Error('Unknown bridge operation');
    if (failed) throw failed;
    if (stopped) throw new Error('Bridge is stopped');
    if (pending) throw new Error('A bridge request is already pending');
    if (!ready && !busy && operation === 'shutdown') {
        stopped = true;
        detachTransport();
        return { state: 'not-started' };
    }
    return new Promise((resolve, reject) => {
        pending = { operation, id, buttons, loading, settings, themes, regulations, prerequisites,
                    resolve, reject, waiting: null, signals: new Set() };
        if (armEntry) armEntry();
    });
}

send({ event: 'waiting', level: 'DEBUG', detail: config.bootstrap ? 'Waiting for the CPython 3.13 library' : 'Waiting for exec before manager bootstrap' });
observer = Process.attachModuleObserver({
    onAdded(module) {
        if (!config.bootstrap) return; // The forked process still contains its parent's Python state.
        // Windows uses its shipped DLL; Linux native tests use a shared CPython library.
        const name = module.name.toLowerCase();
        const python = Process.platform === 'windows'
            ? /^python3\d+[td]?\.dll$/.test(name)
            : Process.platform === 'linux' && /^libpython3\.\d+[td]?\.so(?:\.\d+)*$/.test(name);
        if (!python || armEntry || failed || stopped) return;
        try {
            const supported = Process.platform === 'windows'
                ? name === 'python313.dll'
                : /^libpython3\.13\.so(?:\.\d+)*$/.test(name);
            if (!supported) throw new Error('Unsupported Python library: ' + name);
            if (Process.arch !== 'x64') throw new Error('Bridge requires x64 CPython 3.13');
            function api(name, result, args, scheduling = 'exclusive') {
                return new NativeFunction(module.getExportByName(name), result, args, { scheduling });
            }
            const version = api('Py_GetVersion', 'pointer', []);
            if (!version().readUtf8String().startsWith('3.13.')) throw new Error('Unsupported Python version');
            const initialized = api('Py_IsInitialized', 'int', []);
            const finalizing = api('Py_IsFinalizing', 'int', []);
            const threadState = api('PyThreadState_GetUnchecked', 'pointer', []);
            const interpreter = api('PyThreadState_GetInterpreter', 'pointer', ['pointer']);
            const mainInterpreter = api('PyInterpreterState_Main', 'pointer', []);
            const error = api('PyErr_Occurred', 'pointer', []);
            const clearError = api('PyErr_Clear', 'void', []);
            const addModule = api('PyImport_AddModule', 'pointer', ['pointer']);
            const moduleDict = api('PyModule_GetDict', 'pointer', ['pointer']);
            // Python may join workers/release the GIL. Cooperative execution
            // leaves JavaScript available for cancellation and completion data.
            const evaluate = api('PyRun_StringFlags', 'pointer', ['pointer', 'int', 'pointer', 'pointer', 'pointer'], 'cooperative');
            const utf8 = api('PyUnicode_AsUTF8', 'pointer', ['pointer']);
            const decref = api('Py_DecRef', 'void', ['pointer']);
            const mainName = Memory.allocUtf8String('__main__');
            armEntry = function () {
                if (listener || busy || stopped || failed || (ready && (!pending || pending.waiting))) return;
                listener = Interceptor.attach(module.getExportByName('PyEval_SaveThread'), {
                    onEnter() {
                        // Before SaveThread executes, this thread still holds the GIL and its thread state.
                        // Once bootstrap identifies it, only the main thread may enter Python.
                        if (busy || stopped || failed || (mainThread !== null && this.threadId !== mainThread)) return;
                        if (!initialized() || finalizing()) return;
                        const state = threadState();
                        if (state.isNull() || !interpreter(state).equals(mainInterpreter()) || !error().isNull()) return;
                        // Remove the hook before evaluating: nested GIL releases
                        // and worker joins need no bridge callback at all.
                        detachEntry();
                        busy = true;
                        // Bootstrap always completes its own status first. A queued
                        // control then runs with a known main thread and event scope.
                        const request = (ready && pending) || { operation: 'status', signals: new Set() };
                        request.signals.clear();
                        let result = ptr(0);
                        try {
                            const record = { operation: request.operation };
                            if (request.id) record._control_id = request.id;
                            if (request.operation === 'menu') record.buttons = request.buttons;
                            if (request.operation === 'loading') record.loading = request.loading;
                            if (request.operation === 'settings') record.settings = request.settings;
                            if (request.operation === 'themes') record.themes = request.themes;
                            if (request.operation === 'prerequisites') record.prerequisites = request.prerequisites;
                            if (request.regulations !== undefined) record.regulations = request.regulations;
                            const requestJson = JSON.stringify(JSON.stringify(record));
                            const expression = ready
                                ? config.repeat_prefix + requestJson + ')'
                                : config.prefix + requestJson + config.suffix;
                            const source = Memory.allocUtf8String(expression);
                            const main = addModule(mainName); // Borrowed: never decrement this reference.
                            if (main.isNull()) throw new Error('No Python main module');
                            const globals = moduleDict(main); // Borrowed dictionary, unchanged by our transient namespace.
                            if (globals.isNull()) throw new Error('No Python main dictionary');
                            result = evaluate(source, 258, globals, globals, ptr(0)); // 258 means a Python expression.
                            if (result.isNull()) throw new Error('Manager bootstrap evaluation failed');
                            const text = utf8(result);
                            if (text.isNull()) throw new Error('Manager returned a non-string response');
                            const value = text.readUtf8String();
                            if (value.startsWith('WAIT:')) {
                                if (ready) throw new Error('Unexpected interpreter readiness wait after bootstrap');
                                // Only bootstrap may observe interpreter readiness
                                // until Python can identify its actual main thread.
                                return;
                            }
                            const response = JSON.parse(value);
                            if (response.event === 'waiting' && pending === request) {
                                if (!['imports', 'operations'].includes(response.dependency)) throw new Error('Unknown control dependency');
                                // A completion can arrive while evaluation releases
                                // the GIL, before its WAIT reply reaches JavaScript.
                                request.waiting = request.signals.has(response.dependency) ? null : response.dependency;
                                return;
                            }
                            if (response.event === 'waiting') return; // The caller cancelled during evaluation.
                            if (response.event !== 'manager') {
                                const error = new Error(response.detail || 'Invalid manager response');
                                error.traceback = response.traceback;
                                error.activity = response.activity;
                                throw error;
                            }
                            if (!ready) {
                                ready = true;
                                mainThread = this.threadId;
                                send(response);
                            }
                            for (const notice of response.status.regulation_notices || []) {
                                send({ ...notice, event: 'diagnostic' });
                            }
                            for (const notice of response.status.activity_notices || []) {
                                send({ ...notice, event: 'native-activity' });
                            }
                            if (request.operation === 'shutdown') {
                                stopped = true;
                                detachTransport();
                                if (pending && pending !== request) {
                                    pending.reject(new Error('Bridge is stopped'));
                                    pending = null;
                                }
                            }
                            // Cancellation may queue another request while Python
                            // waits on publication. Its reply belongs to a later
                            // entry, not to the operation captured above.
                            if (pending === request) {
                                const completed = pending;
                                pending = null;
                                completed.resolve(response.status);
                            }
                        } catch (failure) {
                            // We entered with no Python error; clear only errors raised by our own evaluation.
                            clearError();
                            fail(failure);
                        } finally {
                            // The expression result is a new reference, owned by this callback.
                            if (!result.isNull()) decref(result);
                            busy = false;
                            armEntry(); // Bootstrap, or a still-live unparked request, only.
                        }
                    }
                });
                Interceptor.flush();
            };
            armEntry();
            // The runtime has been identified; future controls reuse its APIs.
            setImmediate(() => { if (observer) { observer.detach(); observer = null; } });
            send({ event: 'transport', level: 'DEBUG', detail: 'Waiting for Python main-thread readiness' });
        } catch (failure) { fail(failure); }
    }
});
function receiveRequests() {
    recv('manager-request', message => {
        // Replies are data records; the client owns the timeout and matches replies by request ID.
        try {
            Promise.resolve(enqueue(message.operation, message.id, message.buttons, message.loading, message.settings, message.themes, message.regulations, message.prerequisites)).then(
                status => send({ event: 'reply', id: message.id, status }),
                error => send({ event: 'reply', id: message.id, error: String(error), traceback: error.traceback || error.stack })
            );
        } catch (error) {
            send({ event: 'reply', id: message.id, error: String(error), traceback: error.traceback || error.stack });
        }
        receiveRequests();
    });
}
function receiveCancellation() {
    recv('manager-cancel', message => {
        // A timed-out caller must not leave a request blocking later status/shutdown calls.
        if (pending && pending.id === message.id) {
            const cancelled = pending;
            pending = null;
            if (ready) detachEntry();
            cancelled.reject(new Error(message.detail || 'Bridge request cancelled'));
        }
        receiveCancellation();
    });
}
function receiveCompletion() {
    recv('manager-resume', message => {
        if (pending && pending.id === message.id && ['imports', 'operations'].includes(message.dependency)) {
            pending.signals.add(message.dependency);
            if (pending.waiting === message.dependency) {
                pending.waiting = null;
                armEntry();
            }
        }
        receiveCompletion();
    });
}
receiveRequests();
receiveCancellation();
receiveCompletion();
