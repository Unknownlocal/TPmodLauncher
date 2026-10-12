# TPM_CodeModManager (tentative)

A code mod entry point for Team Principal: A Racing Manager.

# Verification of bundled code

## Verify the bundled Python runtime

These checks verify **Python 3.13.16, Windows x64 embeddable package**. They do
not authenticate our launcher source or Frida. Use trusted verification tools,
not the bundled interpreter you are checking.

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

### Sigstore

Use an independently trusted Python installation for the `python` commands below.
Python.org recommends installing the verifier from its pinned requirements file:

```powershell
python -m pip install -r https://raw.githubusercontent.com/sigstore/sigstore-python/main/install/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Sigstore verifier installation failed.' }
Invoke-WebRequest -UseBasicParsing -Uri "$upstream.sigstore" -OutFile "$archive.sigstore"
python -m sigstore verify identity --bundle "$archive.sigstore" --cert-identity thomas@python.org --cert-oidc-issuer https://accounts.google.com "$archive"
if ($LASTEXITCODE -ne 0) { throw 'Sigstore verification failed.' }
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
$bundled = Join-Path $bundleRoot 'python-3.13.16-embed-amd64'

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
