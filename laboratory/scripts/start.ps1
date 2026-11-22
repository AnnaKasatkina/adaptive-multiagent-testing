param([int]$Port = 8765, [string]$DataPath)
. "$PSScriptRoot\common.ps1"
$tools = Initialize-Lab
if (-not (Test-Path -LiteralPath "$LabRoot\frontend\dist\index.html")) {
    Push-Location "$LabRoot\frontend"
    try { & $tools.Pnpm build; Assert-NativeSuccess 'Сборка интерфейса' } finally { Pop-Location }
}
if (-not $DataPath) { $DataPath = Join-Path $ProjectRoot 'artifacts\library' }
Write-Host "Лаборатория: http://127.0.0.1:$Port · остановка Ctrl+C"
& $LabPython -m cp1 serve --port $Port --data $DataPath
Assert-NativeSuccess 'Сервер'
