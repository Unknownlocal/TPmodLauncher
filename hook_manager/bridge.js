// SPDX-License-Identifier: MPL-2.0
// Trusted transport: run our fixed manager source at a Python main-thread GIL boundary.
const config = __HOOK_CONFIG__;
let observer = null;
let listener = null;
let busy = false;
let ready = false;
let packReported = false;
let stopped = false;
let failed = null;
let pending = null;
let lastAttempt = 0;
let menuPending = false;
let menuState = null;
let settingsWatching = false;
let settingsState = null;
let themesPending = false;
let themesState = null;
let preloadFailureReported = false;
let preloadWatching = false;
let preloadState = null;
let readinessWatching = false;
let readinessState = null;

function detachTransport() {
    // No callback pointers are queued in Python, so these hooks can be detached on unload.
    if (listener) { listener.detach(); listener = null; }
    if (observer) { observer.detach(); observer = null; }
}

function fail(error) {
    failed = String(error).slice(0, 4096);
    send({ event: 'error', detail: failed });
    if (pending) { pending.reject(new Error(failed)); pending = null; }
    setImmediate(detachTransport);
}

function enqueue(operation, id, buttons, loading, settings, themes, regulations, prerequisites) {
    // The RPC thread only queues data; it must never call Python's API itself.
    if (!['status', 'shutdown', 'menu', 'loading', 'settings', 'themes', 'prerequisites', 'adapters', 'regulation-defaults', 'await-regulations', 'prepare-regulations', 'install-regulations', 'activate-regulations', 'stop-regulations', 'retire-regulations', 'restore-native-rules'].includes(operation)) throw new Error('Unknown bridge operation');
    if (failed) throw new Error(failed);
    if (stopped) throw new Error('Bridge is stopped');
    if (pending) throw new Error('A bridge request is already pending');
    if (!ready && operation === 'shutdown') {
        stopped = true;
        detachTransport();
        return { state: 'not-started' };
    }
    return new Promise((resolve, reject) => { pending = { operation, id, buttons, loading, settings, themes, regulations, prerequisites, resolve, reject }; });
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
        if (!python || listener || failed || stopped) return;
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
            // Python execution may release the GIL. Let a worker's SaveThread
            // hook enter JavaScript, see busy and return; holding the JS lock
            // here could strand that worker with the GIL we need to reacquire.
            const evaluate = api('PyRun_StringFlags', 'pointer', ['pointer', 'int', 'pointer', 'pointer', 'pointer'], 'cooperative');
            const utf8 = api('PyUnicode_AsUTF8', 'pointer', ['pointer']);
            const decref = api('Py_DecRef', 'void', ['pointer']);
            const mainName = Memory.allocUtf8String('__main__');
            listener = Interceptor.attach(module.getExportByName('PyEval_SaveThread'), {
                onEnter() {
                    // Before SaveThread executes, this thread still holds the GIL and its thread state.
                    // Skip nested calls, dormant ready sessions, finalization and pre-existing exceptions.
                    if (busy || stopped || failed || (ready && packReported && !pending && !menuPending && !settingsWatching && !themesPending && !preloadWatching && !readinessWatching)) return;
                    if (!initialized() || finalizing()) return;
                    const state = threadState();
                    if (state.isNull() || !interpreter(state).equals(mainInterpreter()) || !error().isNull()) return;
                    if (!pending && Date.now() - lastAttempt < 250) return;
                    lastAttempt = Date.now();
                    busy = true;
                    let result = ptr(0);
                    try {
                        const request = pending || { operation: 'status' };
                        const record = { operation: request.operation };
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
                            // A busy worker must not prevent a nearby main-thread attempt.
                            lastAttempt = 0;
                            return;
                        }
                        const response = JSON.parse(value);
                        if (response.event !== 'manager') throw new Error(response.detail || 'Invalid manager response');
                        if (!ready) {
                            ready = true;
                            send(response);
                        }
                        // Startup can finish after manager readiness; report its pack once per process.
                        const pack = response.status.pack;
                        // Availability precedes career setup and saves queued
                        // by a registry which may no longer be available.
                        const readiness = response.status.readiness;
                        readinessWatching = !!readiness && readiness.watching;
                        if (readiness && JSON.stringify(readiness) !== readinessState) {
                            readinessState = JSON.stringify(readiness);
                            send({ event: 'readiness', status: readiness });
                        }
                        const preload = response.status.adapters && response.status.adapters.career_preload;
                        // Deferred screen imports can fail compatibility after
                        // arming. Keep existing status pulses and report once.
                        preloadWatching = !!preload && preload.state !== 'unavailable';
                        if (preload && JSON.stringify(preload) !== preloadState) {
                            preloadState = JSON.stringify(preload);
                            send({ event: 'career-preload', status: preload });
                        }
                        if (!preloadFailureReported && preload && preload.state === 'unavailable') {
                            preloadFailureReported = true;
                            send({ event: 'diagnostic', level: 'WARNING', component: 'regulations', detail: 'REGULATIONS COMPATIBILITY FAILED career.preload: ' + preload.detail + '; continuing native game' });
                        }
                        for (const notice of response.status.regulation_notices || []) {
                            send({ ...notice, event: 'diagnostic' });
                        }
                        if (!packReported && pack && (pack.state === 'ready' || pack.state === 'unavailable')) {
                            packReported = true;
                            send({ event: 'pack', pack });
                        }
                        // Keep checking until UI import and startup loading finish (or the watchdog fires).
                        const menu = response.status.menu;
                        menuPending = menu.state === 'pending' || (menu.loading && menu.loading.blocked && !menu.loading.timed_out);
                        if (JSON.stringify(menu) !== menuState) {
                            menuState = JSON.stringify(menu);
                            send({ event: 'menu', status: menu });
                        }
                        const settings = response.status.settings;
                        settingsWatching = settings && settings.state !== 'idle' && settings.state !== 'failed';
                        if (settings && JSON.stringify(settings) !== settingsState) {
                            settingsState = JSON.stringify(settings);
                            send({ event: 'settings', status: settings });
                        }
                        const themes = response.status.themes;
                        themesPending = themes && themes.state === 'pending';
                        if (themes && JSON.stringify(themes) !== themesState) {
                            themesState = JSON.stringify(themes);
                            send({ event: 'themes', status: themes });
                        }
                        // Cancellation may queue another request while Python
                        // waits on publication. Its reply belongs to a later
                        // entry, not to the operation captured above.
                        if (pending === request) {
                            const completed = pending;
                            pending = null;
                            if (completed.operation === 'shutdown') {
                                stopped = true;
                                setImmediate(detachTransport);
                            }
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
                    }
                }
            });
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
                error => send({ event: 'reply', id: message.id, error: String(error).slice(0, 4096) })
            );
        } catch (error) {
            send({ event: 'reply', id: message.id, error: String(error).slice(0, 4096) });
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
            cancelled.reject(new Error('Bridge request cancelled'));
        }
        receiveCancellation();
    });
}
receiveRequests();
receiveCancellation();
