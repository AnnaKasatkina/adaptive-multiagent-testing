param([string]$PythonPath, [string]$NodePath, [string]$PnpmPath)
. "$PSScriptRoot\common.ps1"
$tools = [ordered]@{
    Python = Resolve-LabTool $PythonPath 'python' 'python\python.exe'
    Node = Resolve-LabTool $NodePath 'node' 'node\bin\node.exe'
    Pnpm = Resolve-LabTool $PnpmPath 'pnpm' 'bin\fallback\pnpm.cmd'
}
& $tools.Python -c 'import sys; assert sys.version_info[:2] == (3,12), "Требуется Python 3.12"'
Assert-NativeSuccess 'Проверка Python'
& $tools.Node -e 'if(Number(process.versions.node.split(".")[0])<22)process.exit(1)'
Assert-NativeSuccess 'Проверка Node (нужен 22 или новее)'
New-Item -ItemType Directory -Force -Path "$LocalRoot" | Out-Null
$tools | ConvertTo-Json | Set-Content -LiteralPath "$LocalRoot\tools.json" -Encoding utf8
$env:PATH = "$(Split-Path $tools.Node -Parent);$env:PATH"
$env:PYTHONUTF8 = '1'
$venv = Join-Path $LabRoot 'backend\.venv'
if (-not (Test-Path -LiteralPath "$venv\Scripts\python.exe")) { & $tools.Python -m venv $venv; Assert-NativeSuccess 'Создание окружения' }
$python = "$venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath "$venv\Scripts\uv.exe")) { & $python -m pip install uv; Assert-NativeSuccess 'Установка uv' }
Push-Location "$LabRoot\backend"
try { & "$venv\Scripts\python.exe" -m uv sync --frozen --extra test --python $python --cache-dir "$LocalRoot\uv-cache"; Assert-NativeSuccess 'Установка Python-зависимостей' } finally { Pop-Location }
Push-Location "$LabRoot\frontend"
try {
    & $tools.Pnpm install --frozen-lockfile --store-dir "$LocalRoot\pnpm-store"; Assert-NativeSuccess 'Установка интерфейса'
    & $tools.Pnpm build; Assert-NativeSuccess 'Сборка интерфейса'
} finally { Pop-Location }
Write-Host 'Готово. Запуск: .\scripts\start.ps1; проверки: .\scripts\test.ps1 -All'
