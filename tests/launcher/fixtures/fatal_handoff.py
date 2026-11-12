# SPDX-License-Identifier: MPL-2.0
"""Exercise final exception printing in an owned interpreter, never the game."""

import atexit
import ctypes
import json
from pathlib import Path
import sys
from types import ModuleType

sys.path.insert(0, sys.argv[1])
from hook_manager.native_activity import NativeActivity
from hook_manager.native_reporting import NativeReporting
from hook_manager.native_failures import NativeFailures
from hook_manager.native_readiness import NativeReadiness
from launcher.diagnostics.activity_journal import ActivityJournal
from launcher.diagnostics import journal_writer

output, mode = Path(sys.argv[2]), sys.argv[3]
sys.frozen = True  # Model only the frozen Windows gate; no game code is loaded.
activity = NativeActivity(NativeReadiness(), NativeReporting(ActivityJournal(output / "activity.jsonl"), json.loads(sys.stdin.readline())))
atexit.register(lambda: (output / "finalised").touch())
if mode == "closed":
    activity.reporting.writer.close()
elif mode == "formatter":
    def failed_format(record):
        raise RuntimeError("Owned formatter failure")
    journal_writer.format_log_record = failed_format


class RuntimeCrashReporter:
    def record_exception(self, exc_info) -> "None":
        import traceback
        (output / "native-crash.txt").write_text("".join(traceback.format_exception(*exc_info)), encoding="utf-8")
        # Model controller restoration arriving before the native main re-raise.
        activity.stop()


module = ModuleType("runtime_crash_reporter")
module.RuntimeCrashReporter = RuntimeCrashReporter
sys.modules[module.__name__] = module
failures = NativeFailures(activity)
run = ctypes.pythonapi.PyRun_SimpleString
run.argtypes, run.restype = [ctypes.c_char_p], ctypes.c_int
run(b"try:\n raise ValueError('Owned fatal handoff FULL END')\nexcept Exception:\n RuntimeCrashReporter().record_exception(sys.exc_info())\n raise\nfinally:\n (output / 'native-finally').touch()\n")
# PyInstaller's own presentation follows PyErr_Print returning. An owned
# SystemExit handoff must instead finalise Python without reaching this point.
(output / "bootloader-returned").touch()
raise SystemExit(1)
