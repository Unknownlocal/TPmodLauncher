# SPDX-License-Identifier: MPL-2.0
"""Present a finished failure after game cleanup, until explicit dismissal."""

from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import queue
import sys
import threading
import traceback


def _load_module(name):
    # Early dependency failures use the same fixed standard-library sources;
    # neither game folders nor unverified packages enter the import search path.
    spec = importlib.util.spec_from_file_location("_failure_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FailureSession:
    """Print first, then run input and presentation independently of the game."""

    def __init__(self, outcome, *, owned_log=True, ownership=None, input_stream=None, output=None, child_factory=None, host_platform=None):
        self.outcome = deepcopy(outcome)
        self.owned_log = owned_log
        self.ownership = ownership
        self.input_stream = sys.stdin if input_stream is None else input_stream
        self.output = sys.stderr if output is None else output
        self.child_factory = child_factory
        self.host_platform = sys.platform if host_platform is None else host_platform
        self.console_only = self.host_platform.startswith("linux")
        # Proton uses Windows Python and its attached console. The user's host
        # selects desktop features; the running interpreter selects console APIs.
        self._prepare_console = self.host_platform in ("win32", "linux") and os.name == "nt" and input_stream is None and output is None
        self._console_streams = ()
        self.events, self.dismissed = queue.Queue(), threading.Event()
        self.child = self.reader = None
        self._output = None
        self._presentation_errors = []

    def dismiss(self):
        """Queue one idempotent user/owner dismissal without waiting in callbacks."""
        if not self.dismissed.is_set():
            self.events.put(("dismiss", None))

    def _print(self, text):
        self._output.console.enqueue_log_record(text + "\n")

    def _write_presentation_error(self, detail):
        """Queue both outputs independently, including failures before run starts."""
        if self._output is None:
            self._presentation_errors.append(detail)
        else:
            self._output.presentation_error(detail)

    def run(self, *, wait=True):
        """Finish notice output without letting its I/O block input or desktop UI."""
        try:
            return self._run(wait=wait)
        finally:
            if self._output is not None:
                self._output.finish()
            # Writers use private raw descriptors, so these empty original
            # console wrappers are never locked by an output operation.
            for stream in self._console_streams:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass  # Console loss must not replace the original failure.

    def _run(self, *, wait=True):
        """Return only after dismissal; verification-only callers may skip waiting."""
        failure = self.outcome["failure"]
        if wait and self._prepare_console:
            try:
                self.input_stream, self.output = _load_module("windows_console").prepare_console()
                self._console_streams = (self.input_stream, self.output)
            except Exception:
                self._write_presentation_error("Could not show the launcher console:\n" + traceback.format_exc())
        output_type = _load_module("log_output").LogOutput
        path = Path(self.outcome["log_path"]) if self.owned_log and self.outcome["log_written"] else None
        self._output = output_type(path, self.outcome, console=self.output, mode="a",
                                   ownership=self.ownership if self.owned_log else None)
        for detail in self._presentation_errors:
            self._output.presentation_error(detail)
        self._presentation_errors.clear()
        activity = failure.get("activity") or {}
        reason = str(failure["reason"]).splitlines()
        short_reason = (reason[0] if reason else "Unknown failure")[:512]
        notice = dict(session_id=self.outcome["session_id"], title="Mod Launcher could not continue",
                      host_platform=self.host_platform,
                      activity=activity.get("description", "during launcher operation"), reason=short_reason,
                      log_path=self.outcome.get("host_log_path", self.outcome["log_path"]),
                      log_written=self.owned_log and self.outcome["log_written"])
        self._print(notice["title"] + "\nAn error occurred " + notice["activity"] + ".\n" + short_reason)
        if self.console_only and failure.get("stage"):
            self._print("Stage: " + str(failure["stage"]))
        self._print(("Full details: " if notice["log_written"] else "No completed log for this launch: ") + notice["log_path"])
        if failure.get("details"):
            self._print(failure["details"])
        if not wait:
            return
        self._print("Press any key to close this notice and end the launcher console session.")
        if self.console_only and notice["log_written"]:
            self._print("Open the log manually in your editor for full details.")
        try:
            self.reader = _load_module("console_input").ConsoleInput(self.input_stream, lambda event, detail: self.events.put((event, detail)))
            self.reader.start()
            try:
                if self.console_only:
                    # Native Linux desktop UI is deferred. Never start even a
                    # placeholder helper or editor while retaining the console.
                    pass
                elif self.child_factory is not None:
                    self.child = self.child_factory(notice, lambda event, detail: self.events.put((event, detail)))
                else:
                    child_type = _load_module("presentation_child").PresentationChild
                    # Windows venv python.exe can be a redirector. Own the real
                    # interpreter so killing this handle also destroys its UI.
                    command = [sys._base_executable, "-I", "-S", "-B", str(Path(__file__).with_name("presentation_helper.py"))]
                    self.child = child_type(command, notice, lambda event, detail: self.events.put((event, detail)))
                if self.child is not None:
                    self.child.start()
            except Exception:
                self._write_presentation_error("Could not start failure presentation:\n" + traceback.format_exc())
            dialog_ready = False
            while not self.dismissed.is_set():
                event, detail = self.events.get()
                if event in ("key", "dismiss", "dialog-closed") or event == "helper-exited" and detail == 0 and dialog_ready:
                    self.dismissed.set()
                elif event == "dialog-ready":
                    dialog_ready = True
                elif event == "input-unavailable":
                    action = "Close this launcher window to end the notice." if self.console_only else "Close this launcher window or dismiss its dialog to end the notice."
                    self._write_presentation_error("Console input unavailable: " + str(detail) + "\n" + action)
                elif event == "error":
                    self._write_presentation_error(str(detail))
                elif event == "helper-exited":
                    self._write_presentation_error("Presentation helper exited without a displayed dialog: " + str(detail))
        except KeyboardInterrupt:
            self.dismissed.set()  # Preserve the original failed-launch exit code.
        finally:
            self.dismissed.set()
            try:
                if self.child is not None:
                    try:
                        self.child.close()
                    except Exception:
                        self._write_presentation_error("Could not close the presentation helper:\n" + traceback.format_exc())
            finally:
                if self.reader is not None:
                    try:
                        self.reader.close()
                    except Exception:
                        self._write_presentation_error("Could not restore console input:\n" + traceback.format_exc())
