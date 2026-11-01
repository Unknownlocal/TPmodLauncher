# SPDX-License-Identifier: MPL-2.0
"""Initialise the trusted manager only after Python's main thread is identifiable."""


def pulse(sources, digest, instance, game_root, request_json, journal_path=None, reporting_endpoint=None):
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
    # The new background transport must not re-enter a standard-library module
    # whose import we interrupted. Later control/status sources reuse this scope.
    for dependency in ("socket", "_socket", "select", "selectors", "struct", "_struct"):
        if getattr(getattr(sys.modules.get(dependency), "__spec__", None), "_initializing", False):
            return "WAIT: reporting dependency is importing"
    import json
    from types import ModuleType

    # Error metadata must never read readiness from an unverified foreign owner.
    module, activity_provider = None, None
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
            if module.instance == instance:
                activity_provider = module.activity_provider
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
            module.readiness = module.NativeReadiness()
            activity_provider = module.readiness.current_activity
            module.activity_provider = activity_provider
            module.readiness.watch_career()  # Diagnostic activity also covers packs with no code mods.
            module.adapters = module.AdapterModules(module, module.ADAPTER_SOURCES)
            journal = module.ActivityJournal(journal_path) if journal_path is not None else None
            module.activity = module.NativeActivity(module.readiness, journal, reporting_endpoint)
            activity_provider = module.activity.current_activity
            module.activity_provider = activity_provider
            module.discovery = module.PackDiscovery(game_root)
            # Save metadata must survive even when no mod requests validation.
            module.manager.observe("managers.formula_regulation_governance",
                                   lambda imported: module.adapters.start_regulation_changes())
            module.menu = module.MenuAdapter(observe_screen=module.readiness.observe_screen)
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
        elif type(request) is dict and request.get("operation") == "prerequisites":
            if set(request) != {"operation", "prerequisites"}:
                raise ValueError("Invalid prerequisite request fields")
            module.readiness.configure(request["prerequisites"])
            response = module.manager.request({"operation": "status"})
        elif request == {"operation": "adapters"}:
            module.adapters.load()
            response = module.manager.request({"operation": "status"})
        elif request == {"operation": "regulation-defaults"}:
            notices = []
            defaults = None
            try:
                defaults = module.adapters.regulation_defaults()
                if defaults is None:
                    # Keep this same bounded request pending while native imports
                    # continue. No callback or worker is added to the game.
                    return "WAIT: native regulation definitions are loading"
            except Exception as error:
                notices.append(module.build_log_record('WARNING', "REGULATIONS COMPATIBILITY FAILED defaults: "
                               + str(error) + "; default integration skipped", error=error, component="regulations"))
            response = module.manager.request({"operation": "status"})
            response["regulation_defaults"], response["regulation_notices"] = defaults, notices
        elif type(request) is dict and request.get("operation") == "await-regulations":
            notices = []
            try:
                if set(request) not in ({"operation"}, {"operation", "regulations"}):
                    raise ValueError("Invalid regulation loading fields")
                module.adapters.await_regulations(request.get("regulations"))
            except Exception as error:
                notices.append(module.build_log_record('WARNING', "REGULATIONS COMPATIBILITY FAILED career.preload: "
                               + str(error)
                               + "; preload integration skipped; continuing native game", error=error, component="regulations"))
            response = module.manager.request({"operation": "status"})
            response["regulation_notices"] = notices
        elif type(request) is dict and request.get("operation") == "prepare-regulations":
            if set(request) != {"operation", "regulations"}:
                raise ValueError("Invalid regulation control fields")
            notices = []
            module.adapters.prepare_regulations(request["regulations"], submit_log=notices.append)
            response = module.manager.request({"operation": "status"})
            response["regulation_notices"] = notices
        elif type(request) is dict and request.get("operation") in (
                "install-regulations", "activate-regulations", "stop-regulations", "retire-regulations", "restore-native-rules"):
            notices = []
            try:
                if set(request) != {"operation", "regulations"}:
                    raise ValueError("Invalid regulation control fields")
                completed = module.adapters.control_regulations(request["operation"], request["regulations"],
                                                               submit_log=notices.append)
                if not completed:
                    return "WAIT: native regulation operation is active"
            except Exception as error:
                completed = False
                notices.append(module.build_log_record('ERROR', "REGULATIONS COMPATIBILITY FAILED control: "
                               + str(error) + "; regulation control skipped", error=error, component="regulations"))
            response = module.manager.request({"operation": "status"})
            response["regulation_control"] = completed
            response["regulation_notices"] = notices
        else:
            if request == {"operation": "shutdown"}:
                if not module.adapters.close():
                    # Keep guard modules and their channel until every
                    # admitted operation finishes; retry without blocking here.
                    return "WAIT: native regulation operation is active"
                module.menu.close()
                module.themes.close()
                module.settings.close()
                if not module.activity.stop():
                    return "WAIT: native activity records are draining"
                module.readiness.stop()
            response = module.manager.request(request)
        if module.manager.closed:
            module.discovery.stop()
        if module.adapters.preload is not None:
            module.adapters.preload.poll()
        response["adapters"] = module.adapters.status()
        # UI imports and validation can fail between control requests. Drain
        # pack-owned notices through the existing CLI/log transport once.
        notices = response.setdefault("regulation_notices", [])
        # Remove only notices included in this reply: a game worker can append
        # between statements, so extending then clearing could lose a new error.
        while module.adapters.regulation_notices:
            notices.append(module.adapters.regulation_notices.popleft())
        response["pack"] = module.discovery.poll()
        response["menu"] = module.menu.poll()
        loading = response["menu"].get("loading", {})
        if (loading.get("state") in ("complete", "error") or loading.get("timed_out")
                or response["pack"].get("state") == "unavailable"):
            if module.themes.finish_loading():
                module.settings.refresh()
        response["themes"] = module.themes.poll()
        response["settings"] = module.settings.poll()
        response["readiness"] = module.readiness.snapshot()
        response["readiness"]["activity"] = module.activity.snapshot()
        activity_notices = response["activity_notices"] = []
        while module.activity.records:
            activity_notices.append(module.activity.records.popleft())
        return json.dumps({"event": "manager", "status": response}, allow_nan=False)
    except Exception as error:
        # Bootstrap may fail before the shared diagnostics source has loaded.
        import traceback

        return json.dumps({"event": "error", "detail": str(error),
                           "activity": getattr(error, "launcher_activity", None) or (activity_provider() if activity_provider is not None else None),
                           "traceback": "".join(traceback.TracebackException.from_exception(
                               error, max_group_width=float("inf"), max_group_depth=float("inf")).format())})
