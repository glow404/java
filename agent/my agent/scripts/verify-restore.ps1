<#
恢复行为验收入口。
使用端到端验收凭据与证据，在临时 API 容器内检查新恢复库；不改正式库。
#>
param([Parameter(Mandatory=$true)][string]$BackupDirectory, [switch]$Feedback)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$taskBackupDir = (Resolve-Path -LiteralPath $BackupDirectory).Path
$taskRestore = Get-Content -LiteralPath (Join-Path $taskBackupDir 'restore.json') -Raw | ConvertFrom-Json
$taskCredentials = @{}
Get-Content -LiteralPath .env.acceptance | ForEach-Object {
    $taskPart = $_.Split('=',2)
    if ($taskPart.Length -eq 2) { $taskCredentials[$taskPart[0]] = $taskPart[1] }
}
$env:VIBE_ACCEPTANCE_USERNAME = $taskCredentials['USERNAME']
$env:VIBE_ACCEPTANCE_PASSWORD = $taskCredentials['PASSWORD']
$taskScriptDir = (Resolve-Path scripts).Path
$taskEvidenceDir = (Resolve-Path test-results).Path
$taskSession = (Resolve-Path $(if ($Feedback) { '.env.feedback-session.json' } else { '.env.acceptance-session.json' })).Path
$taskVerifyScript = if ($Feedback) { '/verify/verify-feedback-restore.py' } else { '/verify/verify-restore.py' }
$taskRestoredUploads = Join-Path $taskRestore.directory 'uploads'
try {
    # 环境变量只按名称交给 Docker，避免真实值出现在参数或报告中。
    $taskDockerArgs = @('compose','run','--rm','--no-deps','-e','PYTHONPATH=/app',
        '-e','VIBE_ACCEPTANCE_USERNAME','-e','VIBE_ACCEPTANCE_PASSWORD',
        '-v',"${taskScriptDir}:/verify:ro",'-v',"${taskBackupDir}:/verification",
        '-v',"${taskEvidenceDir}:/evidence:ro",'-v',"${taskSession}:/session/session.json:ro",
        '-v',"${taskRestoredUploads}:/restored-uploads",'api','python',$taskVerifyScript)
    & docker @taskDockerArgs
    if ($LASTEXITCODE -ne 0) { throw 'Restore verification failed' }
} finally {
    Remove-Item Env:VIBE_ACCEPTANCE_USERNAME,Env:VIBE_ACCEPTANCE_PASSWORD -ErrorAction SilentlyContinue
}
