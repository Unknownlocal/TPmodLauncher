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
            # Later pulses reuse this callable; do not resend/compile the full
            # infrastructure and adapter source literals for every status poll.
            module.pulse = pulse
            # One trusted adapter scope owns this game process's native changes.
            module.manager = module.Manager()
            # Keep the required-validation lifecycle separate from legacy hooks;
            # no dispatcher or registrations are admitted by bootstrap itself.
            module.callables = module.CallableAdapters()
            module.adapters = module.AdapterModules(module, module.ADAPTER_SOURCES)
            module.discovery = module.PackDiscovery(game_root)
            module.menu = module.MenuAdapter()
            module.themes = module.ThemeAdapter()
            module.settings = module.SettingsAdapter(module.themes)
            module.menu.set_loading(dict(state="loading", loaded=0, failed=0, total=0))
            module.manager.observe(
                "ui.screens.hub_screen",
                lambda imported: module.menu.poll(imported)["state"] != "pending",
            )
            module.manager.observe(
                "ui.screens.options_screen",
                lambda imported: all(status["state"] != "pending" for status in (
                    module.themes.poll(imported), module.settings.poll(imported),
                )),
            )
            for target in ("ui.color_packs", "ui.theme"):
                module.manager.observe(target, lambda imported: module.themes.poll(imported)["state"] != "pending")
            sys.modules[name] = module
        request = json.loads(request_json)
        if type(request) is dict and request.get("operation") in ("menu", "loading", "settings", "themes"):
            field = {"menu": "buttons", "loading": "loading", "settings": "settings", "themes": "themes"}[request["operation"]]
            if set(request) != {"operation", field}:
                raise ValueError("Invalid native UI request fields")
            if field == "buttons":
                module.menu.update(request[field])
            elif field == "loading":
                module.menu.set_loading(request[field])
            elif field == "themes":
                module.themes.update(request[field])
                module.settings.refresh()
            else:
                settings = request[field]
                if type(settings) is not dict or set(settings) != {"records", "result"}:
                    raise ValueError("Invalid settings request fields")
                module.settings.update(settings["records"], settings["result"])
            response = module.manager.request({"operation": "status"})
        elif request == {"operation": "adapters"}:
            module.adapters.load()
            response = module.manager.request({"operation": "status"})
        else:
            if request == {"operation": "shutdown"}:
                if not module.callables.try_stop():
                    # Preserve the queued bridge request for a bounded retry.
                    # Waiting here would pin the main thread needed by the game;
                    # raising would abandon restoration through the fatal path.
                    return "WAIT: native callable operation is active"
                if not module.adapters.close():
                    # Regulation guards own separate registries. Retain their
                    # modules/channel until every admitted operation finishes.
                    return "WAIT: native regulation operation is active"
                module.menu.close()
                module.themes.close()
                module.settings.close()
            response = module.manager.request(request)
        if module.manager.closed:
            module.discovery.stop()
        response["callables"] = (module.callables.status() if module.callables.closed
                                 else module.callables.poll())
        response["adapters"] = module.adapters.status()
        response["pack"] = module.discovery.poll()
        response["menu"] = module.menu.poll()
        loading = response["menu"].get("loading", {})
        if (loading.get("state") in ("complete", "error") or loading.get("timed_out")
                or response["pack"].get("state") == "unavailable"):
            if module.themes.finish_loading():
                module.settings.refresh()
        response["themes"] = module.themes.poll()
        response["settings"] = module.settings.poll()
        return json.dumps({"event": "manager", "status": response}, allow_nan=False)
    except Exception as error:
        return json.dumps({"event": "error", "detail": str(error)[:4096]})
