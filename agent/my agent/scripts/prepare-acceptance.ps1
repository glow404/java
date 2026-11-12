<#
专用浏览器验收账号初始化。
创建随机凭据到被忽略的 .env.acceptance；通过环境变量传入容器，不打印密码。
#>
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$taskCredentialPath = Join-Path (Get-Location) '.env.acceptance'
if (!(Test-Path -LiteralPath $taskCredentialPath)) {
    $taskBytes = New-Object byte[] 24
    $taskGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
    $taskGenerator.GetBytes($taskBytes)
    $taskGenerator.Dispose()
    $taskUsername = 'acceptance_' + (Get-Date -Format 'yyyyMMddHHmmss')
    [IO.File]::WriteAllText($taskCredentialPath, "USERNAME=$taskUsername`nPASSWORD=$([Convert]::ToBase64String($taskBytes))`n", [Text.UTF8Encoding]::new($false))
}
$taskCredentials = @{}
Get-Content -LiteralPath $taskCredentialPath | ForEach-Object { $taskPair = $_ -split '=',2; if ($taskPair.Length -eq 2) { $taskCredentials[$taskPair[0]]=$taskPair[1] } }
$taskOldUsername = $env:VIBE_ACCEPTANCE_USERNAME
$taskOldPassword = $env:VIBE_ADMIN_PASSWORD
try {
    $env:VIBE_ACCEPTANCE_USERNAME = $taskCredentials.USERNAME
    $env:VIBE_ADMIN_PASSWORD = $taskCredentials.PASSWORD
    $taskExists = [string](& docker compose exec -T -e VIBE_ACCEPTANCE_USERNAME api python -c "import os; from app import db; from sqlalchemy import select; c=db.engine.connect(); print(bool(c.execute(select(db.users.c.id).where(db.users.c.username==os.environ['VIBE_ACCEPTANCE_USERNAME'])).first()))")
    if ($LASTEXITCODE -ne 0) { throw 'Unable to check acceptance account' }
    if ($taskExists.Trim() -ne 'True') {
        & docker compose exec -T -e VIBE_ADMIN_PASSWORD api python -m app.cli create-user --username $taskCredentials.USERNAME --password-env VIBE_ADMIN_PASSWORD
        if ($LASTEXITCODE -ne 0) { throw 'Acceptance account creation failed' }
    }
    Write-Output 'Dedicated acceptance account ready. Credentials are in ignored .env.acceptance.'
} finally {
    $env:VIBE_ACCEPTANCE_USERNAME = $taskOldUsername
    $env:VIBE_ADMIN_PASSWORD = $taskOldPassword
}
