# Native Frida Core worker

This helper owns Frida's native device, session, script and child-gating handles.
The Python launcher keeps argument validation, process identity checks, restart
policy, logging and hook-manager cleanup. `launcher/native_worker.py` connects
the existing controller to the helper; it does not load the Frida Python wheel.

The existing backend remains the default. Select the worker explicitly with
`--backend native` before `--` in the Steam launch options. Remove that option
to use the working Python binding again. Both use the same trusted bridge and
manager; neither loads pack-authored code yet.

## Build and check

Use a developer Python 3.13 or later, CMake 3.21 or later and a native C compiler.
On Windows, install Visual Studio's C++ tools. On macOS use Xcode's command-line
tools; on Linux use GCC or Clang and the normal development libraries.

From the repository root:

```sh
python -B native/core_worker/build.py
python -B -m pytest tests/native_worker
```

Check the current Windows launch path with its bundled interpreter:

```text
libs\python-3.13.16-embed-amd64\python.exe -B launcher\entry.py --backend native --check-only
```

Both entry backends enforce the isolated bundled interpreter. The native path
reads runtime settings from `pyproject.toml`; it does not require a Frida wheel or
the Python package manifest. Include `pyproject.toml` in a native runtime profile.
The worker and its portable transport tests can use developer Python on any of
the supported build targets; the production game entry/bridge remains Windows-only.

The build downloads a pinned official devkit, checks its SHA256 and extracts it
into `.cache/core-worker`. `--offline` requires that archive to be cached already.
The worker and its hash manifest go into `libs/core_worker/<os-cpu>/`. Neither the
SDK nor build tools are needed by end users. Windows x64, Linux x64, macOS x64
and macOS arm64 have reviewed input pins and separate CI jobs. This is a shared
source implementation, with one native binary per target.

The runtime verifies the worker against its manifest before running it. That
detects file damage; it does not authenticate a download if both the executable
and manifest were replaced. Release hashes/signatures and dependency notices
remain part of the separate release verification work.

## Transport and shutdown

The worker accepts newline-delimited JSON-RPC 2.0 through private subprocess pipes.
Requests have IDs, deadlines and an 8 MiB frame limit. There is no listening socket
or CLI that attaches to a supplied PID. Attach/resume operations are restricted to
the root process this worker spawned and its gated descendants. Native session
and script handles have separate increasing IDs, so process-ID reuse cannot
identify an older session.

The input reader and cancellation watchdog are separate from Core's event loop.
The existing bridge's cancellation timer can cancel a Core request. Pipe loss or
an explicit close releases suspended children and detaches sessions, with a bound
on cleanup time. The controller first requests normal manager restoration; then
it closes the worker. Unexpected host loss can release native tracking but cannot
prove that Python wrappers were restored in a surviving target.

Game stdout/stderr are piped and forwarded separately from protocol messages.
Game stdin is not forwarded, and this transport is not a terminal emulator.
The current target is a graphical game. Interactive console programs are outside
the current provider contract.

## Tests and measurements

Portable tests use owned processes for script load/post, ordinary output, attach
restrictions and suspended-process release after pipe loss. Windows embedded tests
also run both backends through the same replacement/helper and real Python hook
restoration scenarios, including a main-thread request timeout and retry.

To compare full Windows runtime profiles and repeated owned-manager startup:

```sh
python -B native/core_worker/measure.py --runs 5
```

The result and comparable ZIPs go into `.cache/core-worker/measurements`. Each
profile includes the whole embedded Python runtime, common source/notices and
one backend's actual runtime assets. SDK archives, build intermediates, tests,
study environments and caches are excluded from installed/download totals.
Startup measures dependency setup and readiness on a synthetic Python target;
it is not a live game startup benchmark.

This initial build uses the stock devkit to establish a working baseline. Selecting
QuickJS does not remove V8 or other unused features from the linked stock library.
Reduced builds must rebuild Core/Gum and rerun the same acceptance checks.
The [Frida footprint guidance](https://frida.re/docs/footprint/) explains the
available reductions; its Linux ARM example is not a Windows size guarantee.
