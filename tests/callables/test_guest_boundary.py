# SPDX-License-Identifier: MPL-2.0
"""Real sandbox isolation for the private callable package and native host."""

from mod_runtime.shared_runtime import SharedPythonRunner
from mod_runtime.source import SourceManifest
from tests.source_support import source_snapshot

MANIFEST = SourceManifest.parse(b'''schema = 2
id = "owned.callables"
version = "0.1.0"
api = "0.4.0"
runtime = "python"
entry = "mod"
capabilities = ["diagnostics", "regulations"]
''')


def test_private_native_callable_layer_is_absent_from_real_guest(runner_bundle):
    code = '''import wit_world
import sys
class WitWorld(wit_world.WitWorld):
    def activate(self):
        importer = next(finder for finder in sys.meta_path if type(finder).__name__ == "MemoryImporter")
        for name in ("hook_manager.callables", "_tpm_hook_manager_host"):
            assert name not in sys.modules
            assert importer.find_spec(name) is None
        for name in ("register_callable", "approve", "call_native"):
            assert not hasattr(wit_world, name)
        wit_world.report("Private native hooks absent")
'''
    messages = []
    source = source_snapshot(MANIFEST, (("mod.py", code),))
    with SharedPythonRunner(runner_bundle, (source,), {MANIFEST.id: {"report": messages.append}}) as runner:
        runner.activate(MANIFEST.id)
    assert messages == ["Private native hooks absent"]
