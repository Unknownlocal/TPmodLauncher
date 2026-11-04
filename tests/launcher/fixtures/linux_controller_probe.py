# SPDX-License-Identifier: MPL-2.0
"""Stand in for Proton with a real channel and disposable completed log."""

import importlib.util
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["LINUX_PROBE_ROOT"])
arguments = sys.argv[1:]
session = arguments[arguments.index("--host-session") + 1]
host_log = arguments[arguments.index("--host-log-path") + 1]
spec = importlib.util.spec_from_file_location("owned_proton_entry", root / "launcher/proton_entry.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
capture = {"pid": os.getpid(), "args": arguments, "prefix": os.environ["STEAM_COMPAT_DATA_PATH"], "session": session}
(root / "controller.json").write_text(json.dumps(capture), encoding="utf-8")
boundary = arguments.index("--")
raise SystemExit(module.main(arguments[boundary + 1:], host_session=session, host_log_path=host_log,
                             launcher_arguments=arguments[boundary - 2:boundary]))
