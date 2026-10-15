# Testing policy

Write tests that check meaningful behaviour, edge cases and failure handling.
Each test should catch a failure that matters to the launcher or hook manager.

- Preserve useful checks of behaviour that remains supported.
- Prefer real classes and objects. Mock only dependencies or conditions necessary
  to make a test practical and reliable, and keep each mock as small as possible.
- Use coverage reports to find gaps worth investigating. Add a test when the
  missing behaviour deserves a useful check, rather than to raise a percentage.
- Treat lost coverage as a reason for review, not an automatic failure. Removing
  obsolete code or replacing weak tests can legitimately change coverage. Check
  whether the change loses a useful test of supported behaviour.

Coverage measures which code ran; it does not prove that a test checks the right
result. There is no fixed minimum coverage percentage.

## Platform coverage

The portable Python tests cover both launcher and hook_manager. Native tests
also run the real Frida controller and manager bridge against owned CPython 3.13
helpers on Windows and Linux. They check process replacement, inherited launch
settings, timeouts, hook activation/restoration, shutdown and log replacement.
Linux uses shared-library CPython 3.13 and a verified reduced Frida wheel in a
temporary test bundle. This harness does not relax the production entry point's
requirement to use the bundled Windows interpreter.

Pack-discovery tests check startup snapshots and folder boundaries using owned
modules and folders. The native bridge test also checks discovery after manager
readiness and clears its snapshot at shutdown. It never starts the actual game.

Sandbox-host tests use real Wasmtime and owned Components to check capability
admission, isolated state, memory/fuel/deadline limits, callback failures and
cleanup. They run in the developer environment on each portable CI platform;
the relocated embedded probe also exercises the production host. See
[sandbox host](../launcher/SANDBOX.md) for scope and limits.

Author-contract tests build an owned Python component once per pytest session
with pinned componentize-py and the public WIT. They check real lifecycle/events,
private state, denied host I/O and shipped-interpreter compatibility. Small owned
WAT fixtures check manifest/response errors without repeatedly building Python.
No game code or external service is needed, and builds never import mod source
into the native test interpreter.

The `embedded` tests cover Windows's packaged interpreter and entry point,
including both dependency versions, import isolation, damaged files, offline
startup and real Wasmtime Component execution. The startup audit allows Python's
exact Windows `cmd.exe /c "ver"` platform query; other subprocesses and network
connections remain blocked. These tests skip on Linux. The two `native`
controller/bridge tests must not skip when native acceptance is requested.
`frida_build` identifies the reduced binding's
native feature probe. CI requires these checks in the relevant jobs rather
than counting missing runtime prerequisites as successful acceptance.

## Run the full suite in WSL

Use Ubuntu with the compiler prerequisites in
[FRIDA_BUILD.md](../launcher/FRIDA_BUILD.md), and shared CPython 3.13 with its
development headers. Create a separate Linux venv; a Windows venv is not usable
in WSL. Keep compiler workspaces and pytest temporary files on Linux's filesystem.
When a source build is needed, Python's `--enable-shared` configure option
provides the library used by the native bridge.

From a Linux checkout of this project:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -B launcher/build_frida.py
TPM_FRIDA_BUILD_OUTPUT=.cache/reduced-frida/linux-x86_64 \
TPM_REQUIRE_FRIDA_BUILD_TESTS=1 TPM_REQUIRE_NATIVE_TESTS=1 \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
.venv/bin/python -B -m pytest -p pytest_cov --cov --cov-report=term-missing --cov-report=xml
```

This collects the full suite, including both native tests. Only the
Windows entry-point checks should skip. Missing Python/Frida prerequisites for
the native tests fail when `TPM_REQUIRE_NATIVE_TESTS=1`. Linux helpers exercise
explicit fork/exec with the normal exec-completion handoff before their parent
returns from spawning. Attaching during Python's subprocess fork implementation
stalls with stock and reduced Frida on WSL; that path remains a documented
limitation, not a supported case inferred from these passing tests.

## GitHub Actions

`pytest.yml` runs portable tests on Windows, Linux and GitHub-hosted macOS Intel
and ARM. Its Windows job also prepares and tests the packaged runtime. The
Windows coverage artifact retains its existing `python-coverage` name; other
platforms have separate names.

`reduced-frida.yml` builds the binding on all four targets. Its Linux job runs
the launcher/manager suite with native tests required after compilation. Its
Windows trusted-run job tests the authenticated reduced candidate. macOS
currently runs portable Python and binding checks; the manager's native
transport is not yet adapted or accepted there. No self-hosted macOS runners
are needed, and local WSL runs provide independent Linux evidence.
