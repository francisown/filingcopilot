param(
    [ValidateSet('en', 'zh')][string]$Language = 'en',
    [ValidateSet('core', 'locked')][string]$Profile = 'core',
    [string]$PythonExecutable
)

$ErrorActionPreference = 'Stop'
if ($Language -eq 'zh') {
    Write-Host '安装已开始。正在检查 Python 环境，请稍候…'
} else {
    Write-Host 'Setup has started. Checking the Python environment; please wait…'
}
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
. (Join-Path $PSScriptRoot 'scripts\windows_python.ps1')
$taskVenvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
try {
    if (Test-Path -LiteralPath $taskVenvPython) {
        # Reuse a valid existing environment rather than overwriting it with a
        # different system Python. Configured credentials are never imported.
        $taskPython = Find-FilingCopilotPython -Candidates @([pscustomobject]@{Path=$taskVenvPython; Arguments=@()})
    } else {
        $taskPython = if ($PythonExecutable) {
            Find-FilingCopilotPython -Candidates @([pscustomobject]@{Path=$PythonExecutable; Arguments=@()})
        } else { Find-FilingCopilotPython }
        $taskArguments = @($taskPython.Arguments)
        if ($Language -eq 'zh') {
            Write-Host '正在创建本地 Python 环境，请稍候…'
        } else {
            Write-Host 'Creating the local Python environment; please wait…'
        }
        & $taskPython.Path @taskArguments -m venv .venv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
} catch {
    if ($Language -eq 'zh') {
        Write-Host '需要 Python 3.10 或更高版本。请安装兼容的 Python，或用 -PythonExecutable 指定解释器。已有 .venv 不会被覆盖。'
    } else {
        Write-Host 'Python 3.10 or later is required. Install a compatible Python or use -PythonExecutable to select it. An existing .venv is not overwritten.'
    }
    exit 1
}
if ($Language -eq 'zh') {
    Write-Host 'Python 环境已就绪，开始检查并安装依赖；首次运行可能需要几分钟。'
} else {
    Write-Host 'Python environment is ready. Starting dependency checks and installation; the first run may take a few minutes.'
}
& $taskVenvPython scripts\install_dependencies.py --profile $Profile --language $Language
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($Language -eq 'zh') {
    Write-Host '安装完成。可先使用内置 SEC 快照；配置 .env 后启用 Agent。'
} else {
    Write-Host 'Installation complete. Explore the bundled SEC snapshot, then configure .env for Agent research.'
}
