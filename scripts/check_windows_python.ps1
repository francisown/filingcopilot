param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
. (Join-Path $ProjectRoot 'scripts\windows_python.ps1')
$script:ProbeCalls = @()
function Test-PythonOld {
    $script:ProbeCalls += ,@($args)
    $global:LASTEXITCODE = 1
    '3.9'
}
function Test-PythonNew {
    $script:ProbeCalls += ,@($args)
    $global:LASTEXITCODE = 0
    '3.13'
}
function Test-PythonBroken {
    $script:ProbeCalls += ,@($args)
    $global:LASTEXITCODE = 0
    'not Python'
}
function Assert-Check($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
$taskCandidates = @(
    [pscustomobject]@{Path='Test-PythonOld'; Arguments=@('-3')},
    [pscustomobject]@{Path='Test-PythonNew'; Arguments=@('-3')}
)
$taskSelected = Find-FilingCopilotPython -Candidates $taskCandidates
Assert-Check ($taskSelected.Path -eq 'Test-PythonNew') 'A newer compatible Python must be accepted after an incompatible candidate.'
Assert-Check ($script:ProbeCalls.Count -eq 2) 'Both candidates must be probed without invoking venv or installation.'
Assert-Check ($script:ProbeCalls[1][0] -eq '-3' -and $script:ProbeCalls[1][1] -eq '-c') 'Launcher arguments must be preserved as separate arguments.'
$taskFallback = Find-FilingCopilotPython -Candidates @(
    [pscustomobject]@{Path='Test-PythonBroken'; Arguments=@()},
    [pscustomobject]@{Path='Test-PythonNew'; Arguments=@()}
)
Assert-Check ($taskFallback.Path -eq 'Test-PythonNew') 'An invalid launcher must not prevent a compatible fallback.'
$taskRejected = $false
try {
    Find-FilingCopilotPython -Candidates @([pscustomobject]@{Path='Test-PythonOld'; Arguments=@()}) | Out-Null
} catch {
    $taskRejected = $_.Exception.Message -match 'Python 3.10 or later'
}
Assert-Check $taskRejected 'An unsupported interpreter must fail with actionable English guidance.'
$taskEmpty = $false
try { Find-FilingCopilotPython -Candidates @() | Out-Null } catch { $taskEmpty = $true }
Assert-Check $taskEmpty 'Missing Python must not invoke installation.'
foreach ($taskName in @('setup.ps1','start.ps1','scripts\windows_python.ps1')) {
    $taskTokens = $null
    $taskErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $ProjectRoot $taskName), [ref]$taskTokens, [ref]$taskErrors) | Out-Null
    Assert-Check ($taskErrors.Count -eq 0) ('PowerShell parse errors: ' + $taskName)
}
Write-Output 'Windows Python selection: newer interpreter, fallback, argument forwarding, unsupported/missing interpreter and script parsing passed. No installation or service startup.'
