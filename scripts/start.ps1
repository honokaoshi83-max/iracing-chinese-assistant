$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$python=Join-Path $root '.runtime/python/python.exe'
if (!(Test-Path -LiteralPath $python)) { throw '请先双击安装.cmd完成安装。' }
$cfg=Get-Content -LiteralPath (Join-Path $root 'config/default.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$local=Join-Path $root 'config/local.json'
if (Test-Path -LiteralPath $local) { $lc=Get-Content -LiteralPath $local -Raw -Encoding UTF8 | ConvertFrom-Json; if ($null -ne $lc.port) {$cfg.port=$lc.port} }
$voiceRoot=Join-Path $root 'work/race-voices'
& $python -c 'from app_lifecycle import owner_alive; import sys; sys.exit(0 if owner_alive(sys.argv[1]) else 1)' $voiceRoot
if ($LASTEXITCODE -ne 0) { throw '请先打开播报助手应用，再启动组件。' }
$port=$cfg.port
$identity=(& $python -c 'from runtime_settings import instance_id; print(instance_id())').Trim()
$existing=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    try { $health=Invoke-RestMethod ('http://127.0.0.1:'+$port+'/health') -TimeoutSec 3 } catch { throw ('端口 '+$port+' 被其他程序占用。') }
    if ($health.instance -ne $identity) { throw ('端口 '+$port+' 属于另一个程序或部署目录，请修改本项目端口。') }
}
$flag=Join-Path $voiceRoot 'watchdog.stop'
$scriptPath=Join-Path $voiceRoot 'watchdog.py'
$owner=Get-Content -LiteralPath (Join-Path $voiceRoot 'app_owner.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$token=$owner.token
if ($token -notmatch '^[a-f0-9]{32}$') {throw '应用运行标记无效，请重新打开应用。'}
# A previous desktop generation may still be shutting down. Never remove its
# stop flag or reuse its supervisor while cleanup is in progress.
for ($i=0;$i -lt 80;$i++) {
    $running=Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {$_.CommandLine -and $_.CommandLine.Contains($scriptPath)}
    $old=$running | Where-Object { !$_.CommandLine.Contains($token) -or (Test-Path -LiteralPath $flag) }
    if (!$old) {break}
    Start-Sleep -Milliseconds 500
}
if ($old) {throw '上一次组件仍在关闭，请稍后重试。'}
& $python -c 'from app_lifecycle import owner_alive; import sys; sys.exit(0 if owner_alive(sys.argv[1],sys.argv[2]) else 1)' $voiceRoot $token
if ($LASTEXITCODE -ne 0) {throw '应用已退出，取消启动组件。'}
if (Test-Path -LiteralPath $flag) {Remove-Item -LiteralPath $flag}
if (!$running) { Start-Process -FilePath $python -ArgumentList @('-X','utf8',('"'+$scriptPath+'"'),'--owner-token',$token) -WindowStyle Hidden -WorkingDirectory $voiceRoot -RedirectStandardOutput (Join-Path $voiceRoot 'watchdog.log') -RedirectStandardError (Join-Path $voiceRoot 'watchdog-error.log') | Out-Null }
for ($i=0;$i -lt 60;$i++) {
    try { $health=Invoke-RestMethod ('http://127.0.0.1:'+$port+'/health') -TimeoutSec 1; if ($health.instance -eq $identity) {Write-Host '播报服务已启动。'; exit 0} } catch {}
    Start-Sleep -Milliseconds 500
}
throw '服务尚未就绪，请查看work/race-voices下的错误日志。'
