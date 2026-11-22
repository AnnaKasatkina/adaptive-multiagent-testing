Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:LabRoot = Split-Path $PSScriptRoot -Parent
$script:ProjectRoot = Split-Path $script:LabRoot -Parent
$script:ResultsRoot = Join-Path $script:ProjectRoot 'artifacts\laboratory'
$script:LocalRoot = Join-Path $script:ProjectRoot 'artifacts\local'

function Resolve-LabTool([string]$Explicit, [string]$Command, [string]$Bundled) {
    if ($Explicit) { return (Resolve-Path -LiteralPath $Explicit).Path }
    $candidate = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\$Bundled"
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    $tool = Get-Command $Command -ErrorAction SilentlyContinue
    if ($tool) { return $tool.Source }
    throw "Не найден $Command. Передайте явный путь при запуске setup.ps1."
}

function Get-LabTools {
    $file = Join-Path $script:ProjectRoot 'artifacts\local\tools.json'
    if (Test-Path -LiteralPath $file) { return Get-Content -LiteralPath $file -Raw | ConvertFrom-Json }
    return [pscustomobject]@{
        Python = Resolve-LabTool '' 'python' 'python\python.exe'
        Node = Resolve-LabTool '' 'node' 'node\bin\node.exe'
        Pnpm = Resolve-LabTool '' 'pnpm' 'bin\fallback\pnpm.cmd'
    }
}

function Initialize-Lab {
    $tools = Get-LabTools
    $env:PATH = "$(Split-Path $tools.Node -Parent);$env:PATH"
    $env:PYTHONUTF8 = '1'
    $script:LabPython = Join-Path $script:LabRoot 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $script:LabPython)) { throw 'Сначала выполните scripts\setup.ps1.' }
    return $tools
}

function Assert-NativeSuccess([string]$Operation) {
    if ($LASTEXITCODE -ne 0) { throw "$Operation завершилась с кодом $LASTEXITCODE" }
}
