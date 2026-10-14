# Building reduced Frida

This developer tool builds an experimental reduced `frida-python` wheel using
unmodified upstream sources. The running launcher continues to use its existing
prepared stock package. Release integration follows after build/platform/game
acceptance.

## Requirements

- A developer Python 3.12 or newer and the project's local venv, populated from
  `requirements-dev.txt`. The bundled embeddable runtime lacks development headers.
- Git, Node.js 18 or newer and npm available on PATH.
- Windows: Visual Studio C/C++ tools and Windows SDK, including x86 tools.
- Linux x64: GCC/G++ and 32-bit development support (`gcc-multilib`, `g++-multilib`
  on Ubuntu). This recipe targets glibc Linux; it does not establish musl support.
- macOS: Xcode command-line tools. Native Intel and ARM builds are separate.

## Commands

From the project root, using the Windows developer venv:

```powershell
.venv/Scripts/python.exe -B launcher/build_frida.py
.venv/Scripts/python.exe -B launcher/build_frida.py --verify-only
```

Uses `.venv/bin/python` on Linux/macOS. Each build runs on its native OS/CPU.
An optional `--work-dir` supplies an empty build directory.
Build trees are retained for inspection, including failed builds; their paths
are printed when building.

Outputs are under `.cache/reduced-frida/<target>/` by default. `--output` can
select another fresh output directory. An existing output is reused only after
checking its wheel and build details against the current recipe. Use a new output
directory for an intentional rebuild; existing mismatching output is not replaced.
Downloaded archives are hash-checked and reused from the project-local cache.

## What is pinned and checked

`frida_build.toml` pins the main Frida 17.23.3 commit, lists required submodules,
and sets reduced Meson features, setuptools and SHA-256 hashes for upstream 20261004 SDK/toolchain
archives. It keeps local injection, QuickJS, native interception and embedded
agent/helper assets. Architecture compatibility is explicitly requested so
missing tools fail rather than silently shrink the supported target set.

The tool runs Frida's configure/compile entry points in child Python processes
and uses its existing `FRIDA_EXTENSION` wheel-packaging interface. Those child
processes receive the checked build paths and environment without changing the
build tool's own environment or imports. The clone selects the release tag
and checks its HEAD against the pinned main commit. Submodule commits come from
their verified parents' committed Git links, with no duplicate hash list in the
recipe. Git's `submodule update --init --checkout` selects those recorded commits;
the tool verifies the resulting checkouts before executing upstream code. It
rejects dirty/untracked source files and selects the venv interpreter explicitly
in Meson's native machine file.

The wheel is named/versioned `17.23.3+modlauncher.1` to identify our build. Core
still reports `17.23.3`. Its filename/metadata, stable ABI, platform, required
generated files, licence and compiled-extension hash are checked before publishing
the experimental output. Setuptools, SDKs and compiler tools remain build inputs.

`build-details.json` records the main pin, resolved submodule commits, input
hashes, compiler/tool versions and output hashes. It contains no developer
absolute filesystem paths.
Compiler versions are recorded rather than fixed across every OS; byte-identical
reproducibility is not claimed. `--verify-only` checks consistency and corruption;
an unsigned local details file is not proof of publisher identity. Cached checks
require the current recipe, main pin, complete source-record paths and output
hashes; they do not re-fetch Git repositories to prove those recorded links again.

## Tests and signed verification

`tests/launcher/test_frida_build.py` exercises real Git/ZIP/filesystem checks.
To also test the compiled binding locally on Windows:

```powershell
$env:TPM_FRIDA_BUILD_OUTPUT = '.cache/reduced-frida/windows-x86_64'
$env:TPM_REQUIRE_FRIDA_BUILD_TESTS = '1'
.venv/Scripts/python.exe -B -m pytest tests/launcher/test_frida_build.py
```

The native probe uses owned Python parent/child processes. It checks actual
function replacement/restoration, QuickJS messages, script RPC, child gating,
process stdout and rejection of the
disabled V8/SQLite features. It does not start or modify the game.

On POSIX it explicitly exercises fork and exec, validating both lifecycle
events and their separate sessions. On WSL, attaching during Python 3.12's
subprocess fork implementation stalls with both official and reduced Frida;
that path is not validated by the passing fork/exec probe. The release entry
point and prepared runtime remain Windows-only. The controller and manager
transport now also run against owned Linux CPython 3.13 helpers; see the
[full WSL suite instructions](../tests/README.md).

The `reduced-frida.yml` workflow builds/tests Windows x64, Linux x64, macOS Intel
and macOS ARM on GitHub-hosted runners. Linux can also be built and tested
locally in WSL; macOS acceptance relies on those hosted jobs, not self-hosted
machines. On push/manual runs, GitHub's official attestation action signs
build provenance for both the wheel and its details. PR outputs remain unsigned.
Only build details and coverage are uploaded in this phase; wheels stay in the
build job until the incorporated-component release audit is complete. GitHub
attestation availability depends on repository visibility/plan; remote
builds/signing still need acceptance.

Verify uploaded build details with GitHub CLI, supplying the repository that
built them. Once wheel distribution is enabled after the release audit, verify
the released wheel with the same workflow identity:

```text
gh attestation verify <wheel.whl> --repo <owner/repository> --signer-workflow <owner/repository>/.github/workflows/reduced-frida.yml
gh attestation verify build-details.json --repo <owner/repository> --signer-workflow <owner/repository>/.github/workflows/reduced-frida.yml
```

Then run the local consistency check against the reviewed recipe. Build provenance
establishes which workflow produced those bytes; it does not establish that the
code is harmless.

Upstream references: [building](https://frida.re/docs/building/),
[Python packaging](https://github.com/frida/frida-python/blob/17.23.3/setup.py),
[GitHub attestations](https://github.com/actions/attest).

## Preparing an authenticated Windows candidate

Release preparation also needs GitHub CLI with access to the build repository.
Supply the reviewed launcher commit that produced the build, not the upstream
Frida commit. The expected repository and commit come from your command; build
details cannot choose their own trusted publisher.

```powershell
.venv/Scripts/python.exe -B launcher/prepare_release.py --frida-build .cache/reduced-frida/windows-x86_64 --frida-sources .cache/reduced-frida/windows-x86_64-sources --repository <owner/repository> --source-digest <launcher-commit>
```

This checks wheel, build-details, source archive and source-details attestations against the expected
repository, exact workflow and source/workflow commit, rejecting self-hosted
runners. Python's official archive still passes its existing hash and Sigstore
checks. Unsigned local builds fail; there is no verification bypass option.
See [GitHub's verification options](https://cli.github.com/manual/gh_attestation_verify).

The candidate is prepared below `.cache/releases/reduced/` with its own standard
`pylock.toml`, bundled manifest, runtime, source and notices. The selected stock
package, root lock and root manifest remain intact. Use `--output` with another
fresh folder below `.cache/releases/` to preserve earlier candidates. Package
installation and file verification reuse the existing preparation functions.

To run the existing acceptance suite against this candidate:

```powershell
$env:TPM_TEST_RELEASE_ROOT = '.cache/releases/reduced'
$env:TPM_REQUIRE_EMBEDDED_TESTS = '1'
.venv/Scripts/python.exe -B -m pytest
```

Trusted Windows workflow runs also prepare and test this candidate after signing.
PRs continue unsigned native-binding tests. Candidate preparation authenticates
inputs and retains matching source material. It does not complete every remaining
[component/platform review](../notices/frida-sdk/README.md) or manual Steam
acceptance. Binary uploads remain disabled until those release
requirements are complete. Native macOS/Linux binding builds do not yet provide
a complete launcher/runtime release for those platforms.

## Matching source package

After a build, use its retained workspace to export the exact Frida repositories
and the SDK sources referenced by its native and compatibility dependencies:

```powershell
.venv/Scripts/python.exe -B launcher/frida_sources.py create --work-dir <retained-short-workspace> --build-output .cache/reduced-frida/windows-x86_64 --output .cache/reduced-frida/windows-x86_64-sources
```

The exporter derives SDK commits from the pinned upstream dependency definitions.
It includes their nested submodules and excludes local Git config and hooks.
Cached SDK checkouts must be pristine and match their pins. Original source
notices and our exact build tools are retained. Git bundles preserve source
objects; separate shallow boundaries let the restore command produce valid,
editable checkouts without network access.

For macOS, the exporter also preserves the npm bridge package incorporated into
the Darwin agents under `deps/npm/`. It checks the upstream SHA-512 integrity
pin, the installed lock, and every published file against the actual agent build
inputs. Both agent outputs must exist, including in compatibility builds when
present. The same package is saved once. npm/esbuild remain developer build
tools; they are not added to launcher runtime dependencies. See the
[package notice](../notices/frida-npm/README.md).

The source receipt hashes the archive and binds it to the exact binary build
record. The workflow exports and signs both source files before candidate
preparation. Source archives remain separate build outputs; candidates retain
them under `source/` for review. A future binary download can provide source as
a separate accompanying download; users do not need SDK sources to run Frida.
See [rebuilding and relinking](FRIDA_RELINK.md) for restoration and library changes.
