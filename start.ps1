# Native Windows demo: one local FastAPI process serving the existing frontend and AI.
param([ValidateRange(1024, 65535)][int]$Port = 8001)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Create .venv and install the dependencies listed in README.md first.'
}
foreach ($taskModel in @('rtdetr_r18vd', 'qwen3_vl_2b')) {
    $taskModelDir = Join-Path $PSScriptRoot "models\$taskModel"
    if (-not (Test-Path -LiteralPath (Join-Path $taskModelDir 'config.json'))) {
        throw "Local model configuration missing: $taskModel. See README.md."
    }
    if (-not (Get-ChildItem -LiteralPath $taskModelDir -File | Where-Object { $_.Extension -in '.safetensors', '.bin' })) {
        throw "Local model weights missing: $taskModel. See README.md."
    }
}
if (-not $env:API_KEY) {
    $taskKeyBytes = New-Object byte[] 32
    $taskRandom = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $taskRandom.GetBytes($taskKeyBytes) } finally { $taskRandom.Dispose() }
    $env:API_KEY = [Convert]::ToBase64String($taskKeyBytes)
}
$env:FRONTEND_DIST = Join-Path $PSScriptRoot 'frontend'
$env:ANALYZER_TARGET = 'backend.analyzer:analyze_video'
$env:CATALOG_DIR = Join-Path $PSScriptRoot 'backend\data'
$env:ANALYZER_CATALOG = Join-Path $PSScriptRoot 'backend\data\violation_types.json'
$env:ANALYZER_POLICY = Join-Path $PSScriptRoot 'backend\data\violation_type_policy.json'
$env:ANALYZER_DETECTOR_DIR = Join-Path $PSScriptRoot 'models\rtdetr_r18vd'
$env:ANALYZER_VLM_DIR = Join-Path $PSScriptRoot 'models\qwen3_vl_2b'
$env:DATA_DIR = Join-Path $PSScriptRoot 'var'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:PUBLIC_ORIGIN = ''
$env:CORS_ORIGINS = ''
Write-Host "Forte Vision: http://127.0.0.1:$Port  (stop with Ctrl+C)"
& $taskPython -m uvicorn backend.api.app:app_from_env --factory --host 127.0.0.1 --port $Port --workers 1
exit $LASTEXITCODE
