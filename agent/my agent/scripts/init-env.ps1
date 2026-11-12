<#
生成本地环境配置。
只在 .env 不存在时创建随机数据库密码；模型保持禁用，不生成公开账号密码。
#>
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
if (Test-Path -LiteralPath '.env') { throw '.env already exists; edit it deliberately rather than overwriting.' }
$taskRandom = New-Object byte[] 32
$taskGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
$taskGenerator.GetBytes($taskRandom)
$taskGenerator.Dispose()
$taskPassword = [BitConverter]::ToString($taskRandom).Replace('-','').ToLowerInvariant()
$taskTemplate = Get-Content -LiteralPath '.env.example' -Raw
[IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $taskTemplate.Replace('replace-with-a-long-random-password',$taskPassword), [Text.UTF8Encoding]::new($false))
Write-Output 'Created ignored .env with a random database password and disabled model.'
