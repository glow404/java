<#
本地运行入口。
检查配置和端口，构建并等待服务就绪；管理员仍需明确初始化。
#>
param([int]$Port = 8080)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
if (!(Test-Path -LiteralPath '.env')) { throw 'Copy .env.example to .env and set POSTGRES_PASSWORD first.' }
$taskPortValue = Get-Content -LiteralPath '.env' | Where-Object { $_ -match '^HTTP_PORT=' } | Select-Object -First 1
if ($taskPortValue) { $Port = [int]($taskPortValue -split '=',2)[1] }
$taskListener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
$taskFrontendId = (& docker compose ps -q frontend).Trim()
if ($taskListener -and !$taskFrontendId) { throw "Port $Port is occupied; change HTTP_PORT in .env." }
& docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Invalid Compose configuration' }
& docker compose up -d --build --wait --wait-timeout 300
if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Check docker compose logs.' }
Write-Output "Ready: http://localhost:$Port. Initialize your administrator with docker compose exec api python -m app.cli create-user --username admin"
