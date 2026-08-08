$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StatePath = Join-Path $ProjectRoot "data\runtime\runtime.json"
if (-not (Test-Path -LiteralPath $StatePath)) { Write-Host "No runtime state file found."; exit 0 }
$state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
$entries = @(
    @{name="API"; pid=$state.api_pid; needle="apps.api.main:app"},
    @{name="UI"; pid=$state.ui_pid; needle="apps\research_ui\app.py"},
    @{name="Worker"; pid=$state.worker_pid; needle="scripts\run_file_worker.py"}
)
foreach ($entry in $entries) {
    if (-not $entry.pid) { continue }
    $process = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $entry.pid) -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    if ($process.CommandLine -notlike ("*" + $entry.needle + "*")) {
        Write-Warning ("Skipped PID " + $entry.pid + ": it is not the recorded " + $entry.name + " process.")
        continue
    }
    Stop-Process -Id $entry.pid
}
Move-Item -LiteralPath $StatePath -Destination (Join-Path (Split-Path -Parent $StatePath) "runtime.last-stopped.json") -Force
Write-Host "Stopped API/UI/Worker processes recorded in runtime.json."
