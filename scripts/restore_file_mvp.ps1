param(
    [Parameter(Mandatory=$true)][string]$Backup,
    [Parameter(Mandatory=$true)][string]$Destination
)
$ErrorActionPreference = "Stop"
$Backup = [System.IO.Path]::GetFullPath($Backup)
$Destination = [System.IO.Path]::GetFullPath($Destination)
$ManifestPath = Join-Path $Backup "backup-manifest.json"
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw "backup-manifest.json is missing." }
if ($Destination -eq $Backup -or $Destination.StartsWith($Backup + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Restore destination cannot be inside the backup directory." }
if (Test-Path -LiteralPath $Destination) {
    if (Get-ChildItem -LiteralPath $Destination -Force | Select-Object -First 1) { throw "Restore destination must not exist or must be empty." }
} else {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
}
$manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema_version -ne "backup-manifest/v1") { throw "Unsupported backup manifest version." }
$restored = 0
foreach ($item in @($manifest.files)) {
    $source = [System.IO.Path]::GetFullPath((Join-Path $Backup $item.path))
    $target = [System.IO.Path]::GetFullPath((Join-Path $Destination $item.path))
    if (-not $source.StartsWith($Backup + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Backup path escape: $($item.path)" }
    if (-not $target.StartsWith($Destination + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Restore path escape: $($item.path)" }
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Backup file is missing: $($item.path)" }
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    if ($sourceHash -ne $item.sha256 -or (Get-Item -LiteralPath $source).Length -ne [long]$item.bytes) { throw "Backup verification failed: $($item.path)" }
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    $temporary = $target + ".restore.tmp"
    Copy-Item -LiteralPath $source -Destination $temporary
    if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne $item.sha256) { throw "Post-restore verification failed: $($item.path)" }
    Move-Item -LiteralPath $temporary -Destination $target
    $restored++
}
@{schema_version="restore-report/v1"; restored_at=(Get-Date).ToString("o"); backup=$Backup; files=$restored} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Destination "restore-report.json") -Encoding UTF8
Write-Host "Isolated restore completed: $Destination ($restored files)"
