#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Version = "10.0",
    [string]$Destination = "",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $env:APPDATA "kicad\$Version\scripting\plugins\copper_thief"
}

$resolvedDestination = [System.IO.Path]::GetFullPath($Destination)
$allowedRoot = [System.IO.Path]::GetFullPath((Join-Path $env:APPDATA "kicad"))
if (-not $resolvedDestination.StartsWith($allowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to write outside the KiCad user configuration directory: $resolvedDestination"
}

Write-Host "Destination: $resolvedDestination"

if ($Uninstall) {
    if (Test-Path -LiteralPath $resolvedDestination) {
        Remove-Item -LiteralPath $resolvedDestination -Recurse -Force
        Write-Host "Uninstalled Copper Thief."
    } else {
        Write-Host "Copper Thief is not installed at this destination."
    }
    exit 0
}

$files = @(
    "__init__.py",
    "plugin.py",
    "dialog.py",
    "core.py",
    "board_adapter.py",
    "settings.py",
    "icon.png",
    "README.md",
    "LICENSE"
)

foreach ($file in $files) {
    $source = Join-Path $PSScriptRoot $file
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing plugin file: $source"
    }
}

if (Test-Path -LiteralPath $resolvedDestination) {
    Remove-Item -LiteralPath $resolvedDestination -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $resolvedDestination | Out-Null

foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination $resolvedDestination -Force
}

Write-Host ""
Write-Host "Copper Thief installed successfully."
Write-Host "Restart KiCad, then open PCB Editor and use:"
Write-Host "  Tools -> External Plugins -> Copper Thief"
