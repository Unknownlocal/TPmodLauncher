# SPDX-License-Identifier: MPL-2.0
"""Present a finished failure after game cleanup, until explicit dismissal."""

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import queue
import sys
import threading
import traceback


def _load_class(name, class_name):
    # Early dependency failures use the same fixed standard-library sources;
    # neither game folders nor unverified packages enter the import search path.
    spec = importlib.util.spec_from_file_location("_failure_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, class_name)


class FailureSession:
    """Print first, then run input and presentation independently of the game."""

    def __init__(self, outcome, *, owned_log=True, input_stream=None, output=None, child_factory=None):
        self.outcome = deepcopy(outcome)
        self.owned_log = owned_log
        self.input_stream = sys.stdin if input_stream is None else input_stream
        self.output = sys.stderr if output is None else output
        self.child_factory = child_factory
        self.events, self.dismissed = queue.Queue(), threading.Event()
        self.child = self.reader = None

    def dismiss(self):
        """Queue one idempotent user/owner dismissal without waiting in callbacks."""
        if not self.dismissed.is_set():
            self.events.put(("dismiss", None))

    def _print(self, text):
        try:
            print(text, file=self.output, flush=True)
        except (OSError, ValueError):
            pass  # A closing console cannot suppress log retention or child cleanup.

    def _write_presentation_error(self, detail):
        """Append only to this launch's completed log, still locked by entry."""
        self._print(detail)
        if not self.owned_log or not self.outcome["log_written"]:
            return
        try:
            with Path(self.outcome["log_path"]).open("a", encoding="utf-8") as stream:
                stream.write("\nPRESENTATION ERROR " + json.dumps(detail, ensure_ascii=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, ValueError) as error:
            self.outcome["log_written"] = False
            self._print("Could not append presentation details to the log: " + str(error))

    def run(self, *, wait=True):
        """Return only after dismissal; verification-only callers may skip waiting."""
        failure = self.outcome["failure"]
        activity = failure.get("activity") or {}
        reason = str(failure["reason"]).splitlines()
        short_reason = (reason[0] if reason else "Unknown failure")[:512]
        notice = dict(session_id=self.outcome["session_id"], title="Mod Launcher could not continue",
                      activity=activity.get("description", "during launcher operation"), reason=short_reason,
                      log_path=self.outcome["log_path"], log_written=self.owned_log and self.outcome["log_written"])
        self._print(notice["title"] + "\nAn error occurred " + notice["activity"] + ".\n" + short_reason)
        self._print(("Full details: " if notice["log_written"] else "No completed log for this launch: ") + notice["log_path"])
        if not wait:
            return
        self._print("Press any key to close this notice and end the launcher console session.")
        try:
            self.reader = _load_class("console_input", "ConsoleInput")(self.input_stream, lambda event, detail: self.events.put((event, detail)))
            self.reader.start()
            try:
                if self.child_factory is not None:
                    self.child = self.child_factory(notice, lambda event, detail: self.events.put((event, detail)))
                else:
                    child_type = _load_class("presentation_child", "PresentationChild")
                    # Windows venv python.exe can be a redirector. Own the real
                    # interpreter so killing this handle also destroys its UI.
                    command = [sys._base_executable, "-I", "-S", "-B", str(Path(__file__).with_name("presentation_helper.py"))]
                    self.child = child_type(command, notice, lambda event, detail: self.events.put((event, detail)))
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
                    self._write_presentation_error("Console input unavailable: " + str(detail)
                                                   + "\nClose this launcher window or dismiss its dialog to end the notice.")
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
