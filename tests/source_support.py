# SPDX-License-Identifier: MPL-2.0
"""Create source-mod test inputs without running their Python code."""

import json

from mod_runtime.source import SourceSnapshot


def source_snapshot(manifest, files):
    """Keep the caller's file order and metadata, counting UTF-8 source bytes."""
    files = tuple(files.items()) if isinstance(files, dict) else tuple(files)
    return SourceSnapshot(manifest, files, sum(len(text.encode("utf-8")) for _, text in files))


def write_source_mod(folder, code, *, owner, capabilities=("diagnostics",), api="0.2.0"):
    """Write the usual manifest and source file; tests add any extra files."""
    (folder / "src").mkdir(parents=True)
    (folder / "mod.toml").write_text(f'''schema = 2
id = {json.dumps(owner)}
version = "0.1.0"
api = {json.dumps(api)}
runtime = "python"
entry = "mod"
capabilities = {json.dumps(capabilities)}
''', encoding="utf-8")
    (folder / "src/mod.py").write_text(code, encoding="utf-8")
    return folder
