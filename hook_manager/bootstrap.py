# SPDX-License-Identifier: MPL-2.0
"""Initialise the trusted manager only after Python's main thread is identifiable."""


def pulse(sources, digest, instance, game_root, request_json):
    # sources contains our trusted Python files; digest/instance identify their owner.
    # game_root is the installation folder; request_json contains data only.
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
            for filename, source in sources.items():
                exec(compile(source, filename, "exec"), module.__dict__)
            module.source_digest, module.instance = digest, instance
            # One trusted adapter scope owns this game process's native changes.
            module.manager = module.Manager()
            module.discovery = module.PackDiscovery(game_root)
            module.menu = module.MenuAdapter()
            module.settings = module.SettingsAdapter()
            module.menu.set_loading(dict(state="loading", loaded=0, failed=0, total=0))
            module.manager.observe(
                "ui.screens.hub_screen",
                lambda imported: module.menu.poll(imported)["state"] != "pending",
            )
            module.manager.observe(
                "ui.screens.options_screen",
                lambda imported: module.settings.poll(imported)["state"] != "pending",
            )
            sys.modules[name] = module
        request = json.loads(request_json)
        if type(request) is dict and request.get("operation") in ("menu", "loading", "settings"):
            field = {"menu": "buttons", "loading": "loading", "settings": "settings"}[request["operation"]]
            if set(request) != {"operation", field}:
                raise ValueError("Invalid native UI request fields")
            if field == "buttons":
                module.menu.update(request[field])
            elif field == "loading":
                module.menu.set_loading(request[field])
            else:
                settings = request[field]
                if type(settings) is not dict or set(settings) != {"records", "result"}:
                    raise ValueError("Invalid settings request fields")
                module.settings.update(settings["records"], settings["result"])
            response = module.manager.request({"operation": "status"})
        else:
            if request == {"operation": "shutdown"}:
                module.menu.close()
                module.settings.close()
            response = module.manager.request(request)
        if module.manager.closed:
            module.discovery.stop()
        response["pack"] = module.discovery.poll()
        response["menu"] = module.menu.poll()
        response["settings"] = module.settings.poll()
        return json.dumps({"event": "manager", "status": response}, allow_nan=False)
    except Exception as error:
        return json.dumps({"event": "error", "detail": str(error)[:4096]})
