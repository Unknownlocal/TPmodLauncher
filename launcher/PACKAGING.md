# Create the Windows player bundle

From the repository root, run:

```powershell
.\Package-Release.ps1 -Version 0.1.0
```

This is the maintainer entry point. Players use the resulting ZIP and do not
install Python or developer tools. It produces one Windows x64 bundle;
Proton/Wine use remains experimental and untested.

The workflow works from a source-only checkout. It performs these steps:

1. Create `.venv` if needed and install `requirements-dev.txt`.
2. Fetch Frida at the source revision and submodule pins in `frida_build.toml`,
   download the pinned native SDK inputs, and compile the reduced Windows wheel.
3. Export matching Frida and SDK sources from that same build workspace.
4. Build a fresh trusted runner and prepare the embedded Python and dependencies
   inside an isolated release candidate.
5. Assemble the ZIP, check every extracted file and run its bundled Windows Python
   verification, then write the ZIP and checksum to `dist/`.

No previous `libs/`, binary cache, source export or game installation is required.
The script does not install into the game or upload/publish the release.
Existing versioned outputs are preserved.

## Prerequisites

Install these developer tools once:

- A full Windows x64 Python 3.13 or newer.
- Git on PATH.
- Visual Studio C++ build tools for x64 and x86, with the Windows SDK.

The script uses an existing Windows `.venv` if present; otherwise it creates one
using `py -3` or `python.exe`. `-Python C:\path\to\python.exe` selects the
interpreter for creating a new venv. Native compilation can take several minutes
and the first build downloads upstream source and SDK inputs. The upstream build
locates the installed Visual Studio toolchain.
Node.js and npm are not required by this reduced Windows build. Its JavaScript
runtime is QuickJS, and the optional Frida features that require Node are disabled.

A local compilation can produce a different wheel hash from the old development
installation. The workflow records the new hash in a generated `pylock.toml`
inside its release candidate. Companion dependencies retain their reviewed pins.
The checkout's `pylock.toml`, `bundled_packages.toml` and active `libs/` are left
unchanged. The generated lock and runtime settings also accompany the ZIP under
`source/release/`. This is a local source build, not a GitHub-attested binary.

## Optional modes

Build and verify a candidate without making a ZIP:

```powershell
.\Package-Release.ps1 -Version 0.1.0 -PrepareOnly
```

Recheck the last prepared candidate for that version without setup, downloads,
compilation or an archive:

```powershell
.\Package-Release.ps1 -Version 0.1.0 -CheckOnly
```

Candidates and their local records are retained under `.cache/releases/`;
paired Frida outputs are under `.cache/release-builds/`. Native workspaces are
kept in the system temporary directory, using short paths required by upstream
Windows tools. Their locations are recorded with the build outputs for diagnosis.
A normal run performs a new build; downloaded pinned inputs can be reused.

For deliberate reuse of an existing build/export pair, pass both
`-FridaBuild <directory>` and `-FridaSources <directory>`. The workflow verifies
the source recipe, wheel and exact matching source receipt before preparing the
candidate. Neither override is needed for a clean checkout.

`launcher/package_release.py` remains the lower-level offline archive tool;
normal release preparation uses `Package-Release.ps1` and `release_workflow.py`.

## WSL

Run the same Windows script through WSL's Windows interop; a separate Linux
Python environment is not needed:

```bash
powershell.exe -NoProfile -File "$(wslpath -w ./Package-Release.ps1)" -Version 0.1.0
```

Append `-PrepareOnly` to build without a ZIP, or `-CheckOnly` to recheck an
existing candidate. WSL is a build
shell here; this does not test the launcher under Proton/Wine.

## Output and validation

The command produces:

```text
dist/modLauncher-0.1.0-windows-x64.zip
dist/modLauncher-0.1.0-windows-x64.zip.sha256
```

The packager generates `SteamLaunchOptions.cmd` for Windows and
`SteamLaunchOptions.sh` for Linux inside the ZIP's single `modLauncher/` folder.
Those entry files are not created in the source checkout. Both helpers always
display their platform's launch line and report clipboard-copy success or failure.
The Windows helper uses the bundled interpreter and PowerShell's `Set-Clipboard`.
The Linux helper uses Bash and optional `wl-copy`, `xclip` or `xsel`; it requires
no Linux Python. Shell scripts are stored with LF line endings and executable
permissions in the ZIP. Each helper
does not edit Steam, launch the game, install Python or use a system interpreter.
The Python executable path is absolute; the script path stays relative as
`modLauncher\launcher\entry.py` to keep the Steam launch line short.
The Linux line instead invokes `launcher/launch_proton.sh` before `%command%`.
That wrapper preserves Steam's Proton/runtime command prefix, replaces only the
verified installed game target, and runs the Windows entry adapter through the
bundled Python. The adapter supplies the installed Wine-mapped game path to the
existing executable validator; no fixed drive mapping or Proton path is assumed.

To use the existing Python helper before packaging, run this from the repository
root on Windows, with the prepared runtime present and `modLauncher` installed
beside `TeamPrincipal.exe`:

```powershell
& libs/python-3.13.16-embed-amd64/python.exe -B launcher/steam_launch_options.py
```

Packaging checks the selected dependencies, the runtime against the pinned cached
Python archive, and the trusted runner. It includes original source, notices,
the matching Frida wheel/build details and source archive/receipt under
`source/frida/`. It excludes the game, development environments, tests, local
review documents, caches, logs, saved settings and unselected vendor generations.

Before publishing local output, it extracts the ZIP into fresh staging, compares
every extracted file with the selected inputs and runs the extracted launcher's
real native-import/runner verification. Failed checks produce no final archive.
Existing versioned outputs are never overwritten. Identical inputs and version
use stable ZIP ordering and metadata; compression can vary with the builder's
Python/zlib version.

The checksum detects file changes; it does not authenticate the publisher.
Packaging is separate from upload/publication and from the outstanding items in
[the existing distribution review](../notices/frida-sdk/DISTRIBUTION_REVIEW.md).
Proton/Wine acceptance is not a packaging prerequisite; describe that support as
experimental and untested when releasing.
