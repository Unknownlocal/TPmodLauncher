# SPDX-License-Identifier: MPL-2.0
<#
.SYNOPSIS
Prepare and package the Windows player release with one command.
.EXAMPLE
.\Package-Release.ps1 -Version 0.1.0
.EXAMPLE
.\Package-Release.ps1 -Version 0.1.0 -CheckOnly
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$')]
    [string]$Version,
    [switch]$CheckOnly,
    [string]$Python,
    [string]$FridaBuild,
    [string]$FridaSources
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot

function Assert-WorkspacePath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $boundary = $repoRoot.TrimEnd('\') + '\'
    if (-not $absolute.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Generated path leaves the repository: $absolute"
    }
    $cursor = $absolute
    while ($cursor -ne $repoRoot -and $cursor.Length -ge $repoRoot.Length) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Generated path contains a link or junction: $cursor"
            }
        }
        $cursor = Split-Path -Parent $cursor
    }
    return $absolute
}

function Invoke-Step([string]$Label, [string]$Executable, [string[]]$ToolArguments) {
    Write-Host "`n$Label"
    & $Executable @ToolArguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed (exit $LASTEXITCODE). No later steps were run."
    }
}

Push-Location -LiteralPath $repoRoot
try {
    $developer = Assert-WorkspacePath (Join-Path $repoRoot '.venv\Scripts\python.exe')
    $archive = Assert-WorkspacePath (Join-Path $repoRoot "dist\modLauncher-$Version-windows-x64.zip")
    if (-not $CheckOnly -and ((Test-Path -LiteralPath $archive) -or (Test-Path -LiteralPath "$archive.sha256"))) {
        throw "Version $Version already exists in dist. Choose a new version or move the existing output."
    }

    if (-not (Test-Path -LiteralPath $developer -PathType Leaf)) {
        if ($CheckOnly) {
            throw 'CheckOnly requires an existing developer environment. Run without -CheckOnly to set it up.'
        }
        if ($Python) {
            $bootstrap = $Python
            $bootstrapArguments = @()
        } elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
            $bootstrap = 'py.exe'
            $bootstrapArguments = @('-3')
        } else {
            $bootstrap = (Get-Command python.exe -ErrorAction Stop).Source
            $bootstrapArguments = @()
        }
        Invoke-Step 'Check developer Python (3.13 or newer)' $bootstrap ($bootstrapArguments + @(
            '-I', '-c', "import sys; assert sys.version_info >= (3, 13), 'Developer Python 3.13 or newer is required'"
        ))
        Invoke-Step 'Create developer environment' $bootstrap ($bootstrapArguments + @('-m', 'venv', (Join-Path $repoRoot '.venv')))
    }
    if (-not $CheckOnly) {
        Invoke-Step 'Install pinned developer tools' $developer @('-B', '-m', 'pip', 'install', '-r', 'requirements-dev.txt')
    }

    Write-Host "`nFind the pinned Windows Frida build and its matching source export"
    $resolveArguments = @('-B', 'launcher/package_release.py', '--version', $Version, '--resolve-inputs')
    if ($FridaBuild) { $resolveArguments += @('--frida-build', $FridaBuild) }
    if ($FridaSources) { $resolveArguments += @('--frida-sources', $FridaSources) }
    $resolvedText = & $developer @resolveArguments
    if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the pinned Frida release inputs.' }
    $inputs = ($resolvedText -join "`n") | ConvertFrom-Json
    Write-Host "Build:   $($inputs.build)"
    Write-Host "Sources: $($inputs.sources)"

    if (-not $CheckOnly) {
        $wheelCache = Assert-WorkspacePath $inputs.wheel_cache
        if (-not (Test-Path -LiteralPath $wheelCache)) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $wheelCache) -Force | Out-Null
            Copy-Item -LiteralPath $inputs.wheel -Destination $wheelCache
        }

        # Build into a fresh directory. Keep the previous runner as a reversible backup.
        $generation = [Guid]::NewGuid().ToString('N')
        $newRunner = Assert-WorkspacePath (Join-Path $repoRoot ".cache\release-runner-$generation")
        $oldRunner = Assert-WorkspacePath (Join-Path $repoRoot ".cache\previous-runner-$generation")
        $installedRunner = Assert-WorkspacePath (Join-Path $repoRoot 'libs\mod-runners')
        Invoke-Step 'Build the trusted Python runner' $developer @('-B', '-m', 'mod_runtime.build', '--output', $newRunner)
        New-Item -ItemType Directory -Path (Split-Path -Parent $installedRunner) -Force | Out-Null
        if (Test-Path -LiteralPath $installedRunner) {
            Move-Item -LiteralPath $installedRunner -Destination $oldRunner
        }
        try {
            Move-Item -LiteralPath $newRunner -Destination $installedRunner
        } catch {
            if ((Test-Path -LiteralPath $oldRunner) -and -not (Test-Path -LiteralPath $installedRunner)) {
                Move-Item -LiteralPath $oldRunner -Destination $installedRunner
            }
            throw
        }
        Invoke-Step 'Prepare and authenticate the bundled Python and dependencies' $developer @('-B', 'launcher/prepare_release.py')
    }

    $packageArguments = @('-B', 'launcher/package_release.py', '--version', $Version,
        '--frida-build', $inputs.build, '--frida-sources', $inputs.sources)
    if ($CheckOnly) { $packageArguments += '--check-only' }
    Invoke-Step 'Verify release inputs and package the Windows bundle' $developer $packageArguments
} catch {
    [Console]::Error.WriteLine("Release failed: $($_.Exception.Message)")
    exit 1
} finally {
    Pop-Location
}
