# SPDX-License-Identifier: MPL-2.0
"""Initialise the trusted manager only after Python's main thread is identifiable."""


def pulse(source, digest, instance, request_json):
    # source is our manager snapshot; digest/instance identify it; request_json contains data only.
    import sys

    threads = sys.modules.get("threading")
    if threads is None or getattr(
        getattr(threads, "__spec__", None), "_initializing", False
    ):
        return "WAIT: threading is not ready"
    if threads.current_thread() is not threads.main_thread():
        return "WAIT: other thread"
    import json
    from types import ModuleType

    try:
        if (
            sys.version_info[:2] != (3, 13)
            or not getattr(sys, "_is_gil_enabled", lambda: True)()
        ):
            raise RuntimeError("Bridge requires CPython 3.13 with the GIL enabled")
        name = "_tpm_hook_manager_host"
        module = sys.modules.get(name)
        if module is not None:
            # Never overwrite an unrelated module or another controller's active manager.
            if getattr(module, "source_digest", None) != digest:
                raise RuntimeError(
                    "A different manager already owns the private module name"
                )
            if module.instance != instance and not module.manager.closed:
                raise RuntimeError("Another bridge owns the active manager")
        if module is None or module.instance != instance:
            module = ModuleType(name)
            exec(
                compile(source, "modLauncher/hook_manager/manager.py", "exec"),
                module.__dict__,
            )
            module.source_digest, module.instance = digest, instance
            # No dispatcher or game targets exist until a later sandbox integration phase.
            module.manager = module.Manager()
            sys.modules[name] = module
        response = module.manager.request(json.loads(request_json))
        return json.dumps({"event": "manager", "status": response}, allow_nan=False)
    except Exception as error:
        return json.dumps({"event": "error", "detail": str(error)[:4096]})
