param(
    [string]$BindAddress = "127.0.0.1",
    [int]$ApiPort = 8000,
    [int]$UiPort = 8501,
    [switch]$PromptForWikiApiKey
)
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeDir = Join-Path $ProjectRoot "data\runtime"
New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
$StatePath = Join-Path $RuntimeDir "runtime.json"
if (Test-Path -LiteralPath $StatePath) {
    $existing = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $owned = @(
        @{pid=$existing.api_pid; needle="apps.api.main:app"},
        @{pid=$existing.ui_pid; needle="apps\research_ui\app.py"},
        @{pid=$existing.worker_pid; needle="scripts\run_file_worker.py"}
    ) | Where-Object {
        if (-not $_.pid) { return $false }
        $process = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $_.pid) -ErrorAction SilentlyContinue
        return $process -and $process.CommandLine -like ("*" + $_.needle + "*")
    }
    if (@($owned).Count -gt 0) { throw "Local stack is already running. Stop it before starting another instance." }
    Move-Item -LiteralPath $StatePath -Destination (Join-Path $RuntimeDir "runtime.stale.json") -Force
}
if ($BindAddress -ne "127.0.0.1" -and [string]::IsNullOrWhiteSpace($env:TA_API_KEY)) {
    throw "TA_API_KEY is required when binding to a non-loopback address."
}
if ([string]::IsNullOrWhiteSpace($env:TA_API_KEY)) {
    $env:TA_API_KEY = [guid]::NewGuid().ToString("N")
}
if ($PromptForWikiApiKey -and [string]::IsNullOrWhiteSpace($env:ANTHROPIC_API_KEY) -and [string]::IsNullOrWhiteSpace($env:ANTHROPIC_AUTH_TOKEN)) {
    $secureWikiKey = Read-Host "DeepSeek API key (input is hidden)" -AsSecureString
    $wikiKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureWikiKey)
    try {
        $env:ANTHROPIC_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($wikiKeyPointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($wikiKeyPointer)
    }
    if ([string]::IsNullOrWhiteSpace($env:ANTHROPIC_API_KEY)) { throw "Wiki API key cannot be empty." }
}
if (-not [string]::IsNullOrWhiteSpace($env:ANTHROPIC_API_KEY) -or -not [string]::IsNullOrWhiteSpace($env:ANTHROPIC_AUTH_TOKEN)) {
    if ([string]::IsNullOrWhiteSpace($env:ANTHROPIC_BASE_URL)) { $env:ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic" }
    if ([string]::IsNullOrWhiteSpace($env:TA_WIKI_LLM_MODEL)) { $env:TA_WIKI_LLM_MODEL = "deepseek-v4-flash" }
    $env:TA_WIKI_LLM_ENABLED = "true"
}
$env:PYTHONPATH = $ProjectRoot
$env:TA_PROJECT_ROOT = $ProjectRoot
$ControlRoot = if ([string]::IsNullOrWhiteSpace($env:TA_CONTROL_ROOT)) { Join-Path $ProjectRoot "data\control" } else { [System.IO.Path]::GetFullPath($env:TA_CONTROL_ROOT) }
$env:TA_CONTROL_ROOT = $ControlRoot
$DefaultModelData = Join-Path (Split-Path -Parent $ProjectRoot) "Model\data"
if ([string]::IsNullOrWhiteSpace($env:TA_MODEL_DATA_ROOT) -and (Test-Path -LiteralPath $DefaultModelData)) {
    $env:TA_MODEL_DATA_ROOT = $DefaultModelData
}
$env:TA_API_URL = "http://127.0.0.1:$ApiPort"
$apiOut = Join-Path $RuntimeDir "api.stdout.log"
$apiErr = Join-Path $RuntimeDir "api.stderr.log"
$uiOut = Join-Path $RuntimeDir "ui.stdout.log"
$uiErr = Join-Path $RuntimeDir "ui.stderr.log"
$workerOut = Join-Path $RuntimeDir "worker.stdout.log"
$workerErr = Join-Path $RuntimeDir "worker.stderr.log"
$api = Start-Process -FilePath python -ArgumentList @("-m", "uvicorn", "apps.api.main:app", "--host", $BindAddress, "--port", "$ApiPort") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr -PassThru
$ui = Start-Process -FilePath python -ArgumentList @("-m", "streamlit", "run", "apps\research_ui\app.py", "--server.address", $BindAddress, "--server.port", "$UiPort", "--server.headless", "true") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $uiOut -RedirectStandardError $uiErr -PassThru
$workerId = "local-$([Environment]::MachineName)-worker"
$worker = Start-Process -FilePath python -ArgumentList @("scripts\run_file_worker.py", "--control-root", $ControlRoot, "--worker-id", $workerId) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $workerOut -RedirectStandardError $workerErr -PassThru
$state = @{schema_version="local-runtime/v1"; started_at=(Get-Date).ToString("o"); bind_address=$BindAddress; api_port=$ApiPort; ui_port=$UiPort; api_pid=$api.Id; ui_pid=$ui.Id; worker_pid=$worker.Id; api_url="http://$BindAddress`:$ApiPort"; ui_url="http://$BindAddress`:$UiPort"}
$state | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding UTF8
Write-Host "API: $($state.api_url)"
Write-Host "UI:  $($state.ui_url)"
Write-Host "Worker PID: $($state.worker_pid)"
Write-Host "API key is inherited by child processes only. Do not commit it."
if (-not [string]::IsNullOrWhiteSpace($env:ANTHROPIC_API_KEY) -or -not [string]::IsNullOrWhiteSpace($env:ANTHROPIC_AUTH_TOKEN)) {
    Write-Host "Wiki model: $env:TA_WIKI_LLM_MODEL via $env:ANTHROPIC_BASE_URL"
} else {
    Write-Host "Wiki model: disabled (evidence-only answers remain available)"
}
