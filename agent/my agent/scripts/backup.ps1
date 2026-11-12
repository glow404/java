<#
一致性备份入口。
暂停本项目写入后导出数据库与上传目录，生成 SHA256 清单；不删除数据库或卷。
#>
param([string]$Destination = (Join-Path $PSScriptRoot '../backups'))
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$taskBackupRoot = [IO.Path]::GetFullPath($Destination)
$taskBackupDir = Join-Path $taskBackupRoot (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $taskBackupDir -Force | Out-Null
function Invoke-CheckedDocker {
    param([string[]]$DockerArgs)
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker operation failed ($LASTEXITCODE)" }
}
# 暂停 API 与后台进程，防止文件归档和数据库快照来自不同写入时刻。
Invoke-CheckedDocker -DockerArgs @('compose','stop','frontend','worker','api')
try {
    Invoke-CheckedDocker -DockerArgs @('compose','exec','-T','postgres','pg_dump','-U','vibe','-d','vibe','-Fc','-f','/tmp/vibe-backup.dump')
    # 同时兼容升级前的0002库；新增反馈记录存在时也纳入恢复计数验证。
    $taskExistingTables = @(& docker compose exec -T postgres psql -U vibe -d vibe -t -A -c "SELECT tablename FROM pg_tables WHERE schemaname='public'")
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect backup tables' }
    $taskCountSql = (@('users','sessions','files','tasks','data_tables','data_rows','conversations','messages','results','plans','audits','table_relations','assistant_runs','result_visibility','checkpoints','checkpoint_blobs','checkpoint_writes') | Where-Object { $_ -in $taskExistingTables } | ForEach-Object { "SELECT '$_' AS name, count(*) AS count FROM $_" }) -join ' UNION ALL '
    $taskCounts = & docker compose exec -T postgres psql -U vibe -d vibe -t -A -c "SELECT json_object_agg(name,count) FROM ($taskCountSql) counts"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot capture backup row counts' }
    $taskCounts | Set-Content -LiteralPath (Join-Path $taskBackupDir 'counts.json') -Encoding utf8
    $taskDbContainer = (& docker compose ps -q postgres).Trim()
    $taskApiContainer = (& docker compose ps -a -q api).Trim()
    if (!$taskDbContainer -or !$taskApiContainer) { throw 'Project containers not found' }
    Invoke-CheckedDocker -DockerArgs @('cp',"${taskDbContainer}:/tmp/vibe-backup.dump",(Join-Path $taskBackupDir 'database.dump'))
    Invoke-CheckedDocker -DockerArgs @('cp',"${taskApiContainer}:/data/uploads",(Join-Path $taskBackupDir 'uploads'))
    & tar -czf (Join-Path $taskBackupDir 'uploads.tar.gz') -C $taskBackupDir uploads
    if ($LASTEXITCODE -ne 0) { throw 'File archive creation failed' }
    $taskManifest = @('database.dump','uploads.tar.gz','counts.json') | ForEach-Object { Get-FileHash -LiteralPath (Join-Path $taskBackupDir $_) -Algorithm SHA256 } | Select-Object @{n='File';e={Split-Path $_.Path -Leaf}},Hash
    $taskManifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskBackupDir 'manifest.json') -Encoding utf8
    Write-Output "Backup created: $taskBackupDir"
} finally {
    Invoke-CheckedDocker -DockerArgs @('compose','up','-d','api','worker','frontend')
}
