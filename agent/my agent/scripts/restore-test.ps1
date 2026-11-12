<#
隔离恢复验证入口。
将备份恢复到新建 PostgreSQL 数据库及独立文件目录；不会覆盖应用数据库或上传卷。
#>
param([Parameter(Mandatory=$true)][string]$BackupDirectory)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$taskBackupDir = (Resolve-Path -LiteralPath $BackupDirectory).Path
$taskManifest = Get-Content -LiteralPath (Join-Path $taskBackupDir 'manifest.json') -Raw | ConvertFrom-Json
foreach ($taskEntry in $taskManifest) {
    if ($taskEntry.File -notin @('database.dump','uploads.tar.gz','counts.json')) { throw 'Unexpected backup manifest entry' }
    $taskHash = (Get-FileHash -LiteralPath (Join-Path $taskBackupDir $taskEntry.File) -Algorithm SHA256).Hash
    if ($taskHash -ne $taskEntry.Hash) { throw "Checksum mismatch: $($taskEntry.File)" }
}
$taskDbName = 'vibe_restore_' + (Get-Date -Format 'yyyyMMddHHmmss')
$taskDbContainer = (& docker compose ps -q postgres).Trim()
if (!$taskDbContainer) { throw 'PostgreSQL is not running' }
function Invoke-CheckedDocker {
    param([string[]]$DockerArgs)
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker operation failed ($LASTEXITCODE)" }
}
Invoke-CheckedDocker -DockerArgs @('cp',(Join-Path $taskBackupDir 'database.dump'),"${taskDbContainer}:/tmp/vibe-restore.dump")
# 不使用 --clean，也不 drop 原库。每次只向一个新建数据库恢复。
Invoke-CheckedDocker -DockerArgs @('compose','exec','-T','postgres','createdb','-U','vibe',$taskDbName)
Invoke-CheckedDocker -DockerArgs @('compose','exec','-T','postgres','pg_restore','-U','vibe','-d',$taskDbName,'--no-owner','--exit-on-error','/tmp/vibe-restore.dump')
$taskRestoreDir = Join-Path $taskBackupDir "restored-$taskDbName"
New-Item -ItemType Directory -Path $taskRestoreDir | Out-Null
& tar -xzf (Join-Path $taskBackupDir 'uploads.tar.gz') -C $taskRestoreDir
if ($LASTEXITCODE -ne 0) { throw 'File archive extraction failed' }
Invoke-CheckedDocker -DockerArgs @('compose','exec','-T','postgres','psql','-U','vibe','-d',$taskDbName,'-c',"SELECT schemaname, tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;")
Write-Output "Restored test database: $taskDbName; files: $taskRestoreDir"
@{database=$taskDbName;directory=$taskRestoreDir} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskBackupDir 'restore.json') -Encoding utf8
Write-Output 'Compare account/table/row/result/audit counts and file hashes before declaring recovery verified.'
