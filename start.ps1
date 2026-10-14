param(
    [int]$Port = 0,
    [switch]$NoBrowser,
    [switch]$CheckOnly,
    [ValidateSet('en', 'zh')][string]$Language = 'en'
)

$ErrorActionPreference = 'Stop'
function Get-TaskMessage([string]$English, [string]$Chinese) {
    if ($Language -eq 'zh') { return $Chinese }
    return $English
}
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$taskPort = if ($Port -eq 0) { 8159 } else { $Port }
$taskPortFile = Join-Path $PSScriptRoot '.filingcopilot\web.port'
if ($Port -eq 0 -and (Test-Path -LiteralPath $taskPortFile)) {
    $taskPortText = (Get-Content -LiteralPath $taskPortFile -Raw).Trim()
    if (-not [int]::TryParse($taskPortText, [ref]$taskPort) -or $taskPort -lt 1024 -or $taskPort -gt 65535) {
        throw (Get-TaskMessage 'The saved local service port is invalid.' '本机服务端口记录无效。')
    }
}
if ($taskPort -lt 1024 -or $taskPort -gt 65535) {
    throw (Get-TaskMessage 'The service port must be between 1024 and 65535.' '服务端口须在 1024 到 65535 之间。')
}
$taskUrl = "http://127.0.0.1:$taskPort"
if (-not (Test-Path -LiteralPath $taskPython)) {
    Write-Host (Get-TaskMessage 'Run setup.ps1 to install the environment first.' '请先运行 setup.ps1 安装环境。')
    exit 1
}
$taskVersion = & $taskPython -c 'from filingcopilot import __version__; print(__version__)'
if ($LASTEXITCODE -ne 0) { throw (Get-TaskMessage 'Cannot read the installed version; run setup.ps1 first.' '无法读取已安装版本；请先运行 setup.ps1。') }
$taskProbePorts = @(8123, 8141, 8159, 8151, $taskPort) | Select-Object -Unique
$taskRespondingPorts = 0
foreach ($taskProbePort in $taskProbePorts) {
    try {
        $taskProbe = Invoke-RestMethod -Uri "http://127.0.0.1:$taskProbePort/api/health" -TimeoutSec 1
        if ($taskProbe.version) {
            $taskRespondingPorts += 1
            $taskState = if ($taskProbe.version -eq $taskVersion -and -not $taskProbe.runtime.needs_restart) { Get-TaskMessage 'Versions match' '版本一致' } else { Get-TaskMessage 'Service or resources changed; a manual restart is required' '旧服务或资源已改变，需手动重启' }
            Write-Host (Get-TaskMessage "Port $taskProbePort · running $($taskProbe.version) · installed $taskVersion · $taskState" "端口 $taskProbePort · 运行 $($taskProbe.version) · 已安装 $taskVersion · $taskState")
        }
    } catch { }
}
if ($CheckOnly) {
    if ($taskRespondingPorts -eq 0) {
        Write-Host (Get-TaskMessage 'No FilingCopilot service responded on the checked ports. Run start.ps1 without -CheckOnly to start it.' '检查的端口上没有 FilingCopilot 服务响应。去掉 -CheckOnly 运行 start.ps1 可启动服务。')
    }
    Write-Host (Get-TaskMessage 'Read-only check complete; no service was started, restarted or stopped.' '只读检查完成；未启动、重启或关闭服务。')
    exit 0
}
try {
    $taskHealth = Invoke-RestMethod -Uri "$taskUrl/api/health" -TimeoutSec 2
    if ($taskHealth.version -eq $taskVersion -and -not $taskHealth.runtime.needs_restart) {
        if (-not $NoBrowser) { Start-Process $taskUrl }
        Write-Host (Get-TaskMessage "FilingCopilot $taskVersion is running at $taskUrl." "FilingCopilot $taskVersion 已在 $taskUrl 运行。")
        exit 0
    }
    if ($taskHealth.version) {
        Write-Host (Get-TaskMessage "Running $($taskHealth.version), installed $taskVersion. Close the old service before running this script again." "当前运行 $($taskHealth.version)，已安装 $taskVersion。请先关闭旧服务，再运行启动脚本。")
        exit 1
    }
} catch { }
if (-not $NoBrowser) { Start-Process $taskUrl }
& $taskPython -m filingcopilot serve --port $taskPort
