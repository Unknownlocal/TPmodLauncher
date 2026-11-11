# SPDX-License-Identifier: MPL-2.0
"""Keep the existing launcher import for the shared memory-only error snapshot."""

if __package__:
    from hook_manager.exception_snapshot import ExceptionSnapshot
else:
    # Early log output loads only fixed standard-library sources, before the
    # launcher packages or third-party dependencies are available.
    import importlib.util
    from pathlib import Path
    _source = Path(__file__).resolve().parents[2] / "hook_manager/exception_snapshot.py"
    _spec = importlib.util.spec_from_file_location("_logging_exception_snapshot", _source)
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    ExceptionSnapshot = _module.ExceptionSnapshot
