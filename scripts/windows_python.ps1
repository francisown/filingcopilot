# Find a supported interpreter without creating or changing an environment.
function Find-FilingCopilotPython {
    [CmdletBinding()]
    param([object[]]$Candidates)
    if (-not $PSBoundParameters.ContainsKey('Candidates')) {
        $Candidates = @()
        foreach ($taskName in @('py.exe', 'python.exe', 'python3.exe')) {
            $taskCommand = Get-Command $taskName -CommandType Application -ErrorAction SilentlyContinue
            if ($taskCommand) {
                $taskArguments = if ($taskName -eq 'py.exe') { @('-3') } else { @() }
                $Candidates += [pscustomobject]@{ Path = $taskCommand.Source; Arguments = $taskArguments }
            }
        }
    }
    $taskProbe = 'import sys; print("%d.%d" % sys.version_info[:2]); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
    foreach ($taskCandidate in $Candidates) {
        try {
            $taskArguments = @($taskCandidate.Arguments)
            $taskOutput = & $taskCandidate.Path @taskArguments -c $taskProbe 2>$null
            if ($LASTEXITCODE -eq 0 -and "$taskOutput" -match '^3\.(\d+)$' -and [int]$Matches[1] -ge 10) {
                return $taskCandidate
            }
        } catch { }
    }
    throw 'No supported interpreter was found. Install Python 3.10 or later, or pass -PythonExecutable with its full path.'
}
