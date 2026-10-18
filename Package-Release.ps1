# SPDX-License-Identifier: MPL-2.0
<#
.SYNOPSIS
Prepare and package a Windows or Linux/Proton player release on Windows.
.EXAMPLE
.\Package-Release.ps1 -Version 0.1.0
.EXAMPLE
.\Package-Release.ps1 -Version 0.1.0 -CheckOnly
.EXAMPLE
.\Package-Release.ps1 -Version 0.1.0 -Platform linux
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$')]
    [string]$Version,
    [ValidateSet('windows', 'linux')]
    [string]$Platform = 'windows',
    [switch]$CheckOnly,
    [switch]$PrepareOnly,
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
    if ($CheckOnly -and $PrepareOnly) { throw 'Choose CheckOnly or PrepareOnly, not both.' }
    if (-not $CheckOnly) {
        foreach ($tool in @('git.exe')) {
            if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
                throw "Install $tool before building a release. See launcher/PACKAGING.md for native build prerequisites."
            }
        }
    }
    $developer = Assert-WorkspacePath (Join-Path $repoRoot '.venv\Scripts\python.exe')
    $Platform = $Platform.ToLowerInvariant()
    $platformSuffix = if ($Platform -eq 'linux') { 'linux-proton-x64' } else { 'windows-x64' }
    $archive = Assert-WorkspacePath (Join-Path $repoRoot "dist\modLauncher-$Version-$platformSuffix.zip")
    if (-not $CheckOnly -and ((Test-Path -LiteralPath $archive) -or (Test-Path -LiteralPath "$archive.sha256"))) {
        throw "Version $Version for $Platform already exists in dist. Choose a new version or move that platform's existing output."
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

    $workflowArguments = @('-B', 'launcher/release_workflow.py', '--version', $Version, '--platform', $Platform)
    if ($FridaBuild) { $workflowArguments += @('--frida-build', $FridaBuild) }
    if ($FridaSources) { $workflowArguments += @('--frida-sources', $FridaSources) }
    if ($CheckOnly) { $workflowArguments += '--check-only' }
    if ($PrepareOnly) { $workflowArguments += '--prepare-only' }
    Invoke-Step "Build, prepare and verify the $Platform release" $developer $workflowArguments
} catch {
    [Console]::Error.WriteLine("Release failed: $($_.Exception.Message)")
    exit 1
} finally {
    Pop-Location
}
