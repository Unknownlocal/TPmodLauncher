# Create the Windows player bundle

From the repository root, run:

```powershell
.\Package-Release.ps1 -Version 0.1.0
```

This is the maintainer entry point. Players use the resulting ZIP and do not
install Python or developer tools. It produces one Windows x64 bundle;
Proton/Wine use remains experimental and untested.

The script performs the preparation and packaging steps in order:

1. Create `.venv` if needed and install `requirements-dev.txt`.
2. Find and verify the Windows Frida build selected by `pylock.toml`, then find
   its matching source export. If multiple matching exports exist, use the most
   recently written receipt.
3. Restore the selected wheel to its cache path if missing.
4. Build a fresh trusted runner, retaining the previous runner in `.cache/`.
5. Prepare and authenticate the bundled Python and package dependencies.
6. Assemble the ZIP, check its extracted files and run its bundled Windows Python
   verification, then write the final ZIP and checksum to `dist/`.

Run with the game and launcher closed. Preparation may download pinned inputs;
it does not upload or publish the release, change dependency pins, or overwrite
an existing versioned archive.

## Prerequisites and optional arguments

Install a full developer Python 3.13 or newer. The script uses the existing
Windows `.venv` if present; otherwise it creates one using `py -3` or `python.exe`.
`-Python C:\path\to\python.exe` selects the interpreter for creating a new venv.

Retain the reviewed Frida wheel/build-details pair and its matching source
archive/receipt under `.cache/reduced-frida/<directory>/`. They are local build
artifacts, not part of a source-only checkout. The script finds them by hashes
and the current recipe, not by fixed directory names. These are the only release
materials it cannot recreate automatically: a fresh native compilation can
produce different bytes from the selected wheel. If absent, restore the retained
artifacts, or make an intentional dependency update using
[the Frida build workflow](FRIDA_BUILD.md). The script reports which input is
missing and stops.

If those materials are stored elsewhere, `-FridaBuild <directory>` and
`-FridaSources <directory>` override discovery. The exact pinned wheel and source
checks still apply.

For verification without setup, downloads, rebuilding or an archive:

```powershell
.\Package-Release.ps1 -Version 0.1.0 -CheckOnly
```

This mode requires the developer environment and prepared runtime to exist.
`launcher/package_release.py` remains the lower-level offline archive tool; normal
release preparation should use the PowerShell script.

## WSL

Run the same Windows script through WSL's Windows interop; a separate Linux
Python environment is not needed:

```bash
powershell.exe -NoProfile -File "$(wslpath -w ./Package-Release.ps1)" -Version 0.1.0
```

Append `-CheckOnly` for verification without creating a bundle. WSL is a build
shell here; this does not test the launcher under Proton/Wine.

## Output and validation

The command produces:

```text
dist/modLauncher-0.1.0-windows-x64.zip
dist/modLauncher-0.1.0-windows-x64.zip.sha256
```

The packager generates `SteamLaunchOptions.cmd` inside the ZIP's single
`modLauncher/` folder. It does not create that file in the source checkout.
That helper prints a launch line based on the installed
folder and manifest-selected interpreter, and waits for the user to copy it. It
does not edit Steam, launch the game, install Python or use a system interpreter.
The Python executable path is absolute; the script path stays relative as
`modLauncher\launcher\entry.py` to keep the Steam launch line short.

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
