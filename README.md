# TPM_CodeModManager (tentative)

A code mod entry point for Team Principal: A Racing Manager.

## Writing a code mod

Mod authors write against the launcher's supplied [API contract](hook_manager/api/mod.wit).
They do not need to write or change that WIT file. The build tool generates Python
bindings from it, so Python mods can implement callbacks and call approved host
functions through `wit_world`.

**The mod author builds and supplies `mod.wasm`, using our developer build tool.**
The build tool lives in the launcher repository, but building is a separate
developer command. Normal launcher startup does not compile source or install
build tools. Players receive the compiled component with the mod; launcher
maintainers do not need to build every author's mod.

1. Create a folder containing `mod.toml` and `src/mod.py`. Start from the
   [diagnostic example](examples/diagnostic_mod).
2. Give the mod its own ID and version in `mod.toml`, select the supported API
   version and request only the permissions it needs. See the
   [complete manifest format](hook_manager/api/README.md#mod-layout).
3. Implement `activate()` and `on_event(name, payload)` in a
   `WitWorld(wit_world.WitWorld)` class. `activate` runs once; `on_event` receives
   and returns JSON strings for events delivered by the host. With the
   `diagnostics` permission, `wit_world.report("message")` sends a diagnostic
   message to the host.
4. Set up the [developer environment](hook_manager/api/README.md#build-python-components).
   From the launcher checkout, use that environment's Python to build:

   ```console
   python -m launcher.build_mod path/to/my_mod --output path/to/my_mod/mod.wasm
   ```

   Choose a new output path when rebuilding; the tool refuses to overwrite an
   existing component.
5. Distribute the component, manifest and readable source together. The intended
   installation layout is:

   ```text
   <game-root>/
     modLauncher/
     data/<selected-data-mod>/
       <code-mod>/
         mod.toml
         mod.wasm
         src/mod.py
   ```

Several code mods can live under the same data pack. When the game reports its
selected Formula pack, the launcher discovers its direct child code mods,
validates them and activates each admitted component in its own sandbox.
It logs activation and diagnostics; no new menu button is added yet.
The current contract supports diagnostics and JSON event exchange; it exposes no
game operations. New game capabilities require reviewed additions to the
launcher API. Editing a mod's own
WIT file or inventing an event name cannot grant access to game functions.

The [author guide](hook_manager/api/README.md) explains the API, permissions,
build process and execution budgets, including what fuel counts and what happens
when a mod exceeds its limits.
See [loading rules](hook_manager/api/README.md#discovery-and-lifetime) for ordering,
duplicate IDs, limits and cleanup when the game replaces its process.

# Verification of bundled code

## Verify the bundled Python runtime

These checks verify **Python 3.13.16, Windows x64 embeddable package**. They do
not authenticate our launcher source or Frida. Use trusted verification tools,
not the bundled interpreter you are checking.

The bundled runtime is in `libs/python-3.13.16-embed-amd64/`.

The signature commands below follow [Python.org's Sigstore instructions](https://www.python.org/downloads/metadata/sigstore/)
and its linked [OpenPGP instructions](https://www.python.org/downloads/metadata/pgp/),
with the artifact, signing identity and Windows command syntax substituted for
this release. The SHA-256 is published on the [official release page](https://www.python.org/downloads/release/python-31316/).

### Download the official comparison archive

Open Windows PowerShell in the folder containing this README. Run these blocks
in the same session. This creates a new temporary verification directory and
downloads directly from Python.org without executing the bundled software.

```powershell
$ErrorActionPreference = 'Stop'
$bundleRoot = (Get-Location).Path
$artifact = 'python-3.13.16-embed-amd64.zip'
$upstream = "https://www.python.org/ftp/python/3.13.16/$artifact"
$verifyDir = Join-Path ([IO.Path]::GetTempPath()) ('modLauncher-verify-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $verifyDir | Out-Null
$archive = Join-Path $verifyDir $artifact
Invoke-WebRequest -UseBasicParsing -Uri $upstream -OutFile $archive
```

### SHA-256

Independently check the expected value against the **Windows embeddable package
(64-bit)** row on Python.org's release page:

```text
97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297
```

```powershell
$expectedHash = '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297'
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expectedHash) {
    throw 'Official archive SHA-256 mismatch.'
}
'Archive SHA-256 matches.'
```

### Disposable verification tools

Use a full, independently trusted Python 3.13 or newer installation for this step.
The environment is created inside the temporary verification directory; no tools
are installed into your existing Python installation or the bundled interpreter.
Keep using this PowerShell session for the remaining checks.

```powershell
$verifyTools = Join-Path $verifyDir 'tools'
python -I -m venv "$verifyTools"
if ($LASTEXITCODE -ne 0) { throw 'Verification environment creation failed.' }
$verifyPython = Join-Path $verifyTools 'Scripts/python.exe'

function Invoke-VerificationPython {
    # Disable pip configuration only for this call and restore the caller's value,
    # including on an installation or verification failure.
    $previousPipConfig = $env:PIP_CONFIG_FILE
    try {
        $env:PIP_CONFIG_FILE = 'nul'
        & $verifyPython -I @args
        if ($LASTEXITCODE -ne 0) { throw 'Verification tool command failed.' }
    }
    finally {
        $env:PIP_CONFIG_FILE = $previousPipConfig
    }
}
```

### Sigstore

Python.org recommends installing the verifier from its pinned requirements file:

```powershell
Invoke-VerificationPython -m pip --isolated install -r https://raw.githubusercontent.com/sigstore/sigstore-python/main/install/requirements.txt
Invoke-WebRequest -UseBasicParsing -Uri "$upstream.sigstore" -OutFile "$archive.sigstore"
Invoke-VerificationPython -m sigstore verify identity --bundle "$archive.sigstore" --cert-identity thomas@python.org --cert-oidc-issuer https://accounts.google.com "$archive"
```

Check the Python 3.13 identity and issuer independently in Python.org's table.
A successful verification reports `OK` for the archive. Python.org also links
`cosign` for users who prefer an independently obtained standalone verifier.

### GPG / OpenPGP

Use an independently obtained GnuPG installation. Python.org's OpenPGP page
identifies **Steve Dower** as the Windows binary signer; this is distinct from
the release-manager identity used in the Sigstore instructions. Check the full
fingerprint using the public-key link on that official page:

```text
7ED1 0B65 31D7 C8E1 BC29 6021 FC62 4643 4870 34E5
```

```powershell
Invoke-WebRequest -UseBasicParsing -Uri "$upstream.asc" -OutFile "$archive.asc"
gpg --recv-keys 7ED10B6531D7C8E1BC296021FC624643487034E5
if ($LASTEXITCODE -ne 0) { throw 'Signing key retrieval failed.' }
gpg --fingerprint 7ED10B6531D7C8E1BC296021FC624643487034E5
if ($LASTEXITCODE -ne 0) { throw 'Signing key inspection failed.' }
gpg --verify "$archive.asc" "$archive"
if ($LASTEXITCODE -ne 0) { throw 'GPG verification failed.' }
```

Require a valid signature from the expected key. A keyserver download alone
does not establish the owner's identity; compare the fingerprint with the key
linked by Python.org. GPG can report a valid signature while separately warning
that you have not assigned trust to the key.

### Compare the bundled files with the verified archive

Archive verification alone does not check the extracted Python folder shipped
with this project. Complete this comparison after verifying the archive. It
checks all files, including `python313._pth`, and detects missing, changed or
extra files without using our manifest or running bundled Python.

```powershell
$reference = Join-Path $verifyDir 'official-python'
Expand-Archive -LiteralPath $archive -DestinationPath $reference
$bundled = Join-Path $bundleRoot 'libs/python-3.13.16-embed-amd64'

function Get-RuntimeHashes([string]$Directory) {
    $root = Get-Item -LiteralPath $Directory -Force
    if (-not $root.PSIsContainer) { throw "Not a directory: $Directory" }
    if ($root.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Linked directory requires separate inspection: $Directory"
    }
    $prefix = $root.FullName.TrimEnd('\') + '\'
    $hashes = @{}
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Recurse -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Linked path requires separate inspection: $($item.FullName)"
        }
        if (-not $item.PSIsContainer) {
            $name = $item.FullName.Substring($prefix.Length)
            $hashes[$name] = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
        }
    }
    return $hashes
}

$officialFiles = Get-RuntimeHashes $reference
$bundledFiles = Get-RuntimeHashes $bundled
if ($officialFiles.Count -eq 0) { throw 'Official comparison directory is empty.' }
$differences = @(
    foreach ($name in $officialFiles.Keys) {
        if (-not $bundledFiles.ContainsKey($name)) { "Missing: $name" }
        elseif ($officialFiles[$name] -ne $bundledFiles[$name]) { "Changed: $name" }
    }
    foreach ($name in $bundledFiles.Keys) {
        if (-not $officialFiles.ContainsKey($name)) { "Extra: $name" }
    }
)
if ($differences.Count -gt 0) {
    $differences
    throw 'Bundled Python differs from the verified official archive.'
}
"PASS: all $($officialFiles.Count) runtime files match; no extra files."
```

## Verify the bundled Frida packages

The release contains prepared packages under libs/vendor/, rather than requiring
a wheel or pip at startup. Obtain the original wheel independently for comparison.
The expected SHA-256 is published on [the exact PyPI file entry](https://pypi.org/project/frida/17.23.3/#frida-17.23.3-cp37-abi3-win_amd64.whl).

Run these blocks in the same verification session as the Python checks above:

```powershell
$fridaFile = 'frida-17.23.3-cp37-abi3-win_amd64.whl'
$fridaWheel = Join-Path $verifyDir $fridaFile
$fridaUrl = 'https://files.pythonhosted.org/packages/5e/8f/8fe0179ec76deb88f786be19158322d0f867479d80a229a40d51fe334cbe/frida-17.23.3-cp37-abi3-win_amd64.whl'
$fridaHash = '88b1f07514c38650faf6e803ed13dfaec1ba94efa4c8ed34c1525f8c151689d4'
Invoke-WebRequest -UseBasicParsing -Uri $fridaUrl -OutFile $fridaWheel
if ((Get-FileHash -LiteralPath $fridaWheel -Algorithm SHA256).Hash -ne $fridaHash) {
    throw 'Official Frida wheel SHA-256 mismatch.'
}
'Official Frida wheel SHA-256 matches.'
```

Verify the publishing attestation against the expected upstream repository,
using an independently trusted Python environment and the
[PyPI attestation verifier](https://docs.pypi.org/attestations/consuming-attestations/):

```powershell
Invoke-VerificationPython -m pip --isolated install pypi-attestations==0.0.30
Invoke-VerificationPython -m pypi_attestations verify pypi --repository https://github.com/frida/frida "$fridaWheel"
```

### Compare the prepared package directory

Pip preserves Frida's code, native extension and licence, but adds installation
metadata and rewrites RECORD. To check the complete directory, reproduce that
installation using the disposable verification environment and pinned pip version.
That environment is independent of the launcher and uses your trusted Python.

```powershell
Invoke-VerificationPython -m pip --isolated install pip==26.2.1

$fridaReference = Join-Path $verifyDir 'reference-frida'
$fridaRequirements = Join-Path $verifyDir 'frida-requirements.txt'
[IO.File]::WriteAllText($fridaRequirements, ("frida==17.23.3 --hash=sha256:$fridaHash" + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
Invoke-VerificationPython -m pip --isolated --disable-pip-version-check install --no-index --find-links "$verifyDir" --require-hashes --only-binary=:all: --ignore-installed --no-compile --no-cache-dir --platform win_amd64 --python-version 3.13 --implementation cp --abi cp313 --abi abi3 --abi none --target "$fridaReference" -r "$fridaRequirements"

$manifestPath = Join-Path $bundleRoot 'bundled_packages.toml'
$directoryLines = @(Select-String -LiteralPath $manifestPath -Pattern '^directory = "(libs/vendor/[a-f0-9]{64})"$')
if ($directoryLines.Count -ne 1) { throw 'Expected one canonical package directory in the manifest.' }
$vendorRelative = $directoryLines[0].Matches[0].Groups[1].Value
$vendor = Join-Path $bundleRoot $vendorRelative

# Reuse the trusted PowerShell file-inventory function defined above.
$referenceFiles = Get-RuntimeHashes $fridaReference
$packageFiles = Get-RuntimeHashes $vendor
$packageDifferences = @(
    foreach ($name in $referenceFiles.Keys) {
        if (-not $packageFiles.ContainsKey($name)) { "Missing: $name" }
        elseif ($referenceFiles[$name] -ne $packageFiles[$name]) { "Changed: $name" }
    }
    foreach ($name in $packageFiles.Keys) {
        if (-not $referenceFiles.ContainsKey($name)) { "Extra: $name" }
    }
)
if ($packageDifferences.Count -gt 0) {
    $packageDifferences
    throw 'Bundled packages differ from the independently prepared official wheel.'
}
"PASS: all $($referenceFiles.Count) prepared package files match; no extra files."
```

The manifest supplies only the directory location for this comparison; its
claimed hashes are not the reference. The reference comes from the independently
authenticated upstream wheel and trusted pip. These checks verify third-party
material, not our launcher source or the final release archive's source identity.

The temporary verification directory can be removed after these checks; it
contains the disposable tools and comparison files. Your existing Python
installation and its pip configuration remain unchanged.
