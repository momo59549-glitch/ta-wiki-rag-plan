param([string]$Destination = "")
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($Destination)) { $Destination = Join-Path $ProjectRoot ("backups\" + (Get-Date -Format "yyyyMMdd-HHmmss")) }
$Destination = [System.IO.Path]::GetFullPath($Destination)
if ($Destination -eq $ProjectRoot -or $ProjectRoot.StartsWith($Destination, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Backup destination cannot be the project root or its parent." }
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
$items = @(
    "data\control", "data\knowledge", "data\rule_registry", "data\audit",
    "data\universes", "data\manifests", "data\books",
    "data\research_runs", "data\research_cases", "data\research_cases_full", "data\research_cases_pit_2026",
    "data\market_reports", "data\tushare_sync", "data\batch_checkpoints", "data\batch_logs"
)
foreach ($relative in $items) {
    $source = Join-Path $ProjectRoot $relative
    if (Test-Path -LiteralPath $source) {
        $target = Join-Path $Destination $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $target -Recurse -Force
    }
}
$hashes = Get-ChildItem -LiteralPath $Destination -File -Recurse | ForEach-Object { [pscustomobject]@{path=$_.FullName.Substring($Destination.Length).TrimStart('\'); sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash; bytes=$_.Length} }
@{schema_version="backup-manifest/v1"; created_at=(Get-Date).ToString("o"); project_root=$ProjectRoot; files=$hashes} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Destination "backup-manifest.json") -Encoding UTF8
Write-Host "Backup completed: $Destination"
