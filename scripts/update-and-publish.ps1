[CmdletBinding()]
param(
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $repositoryRoot '.codex\logs'
$logPath = Join-Path $logDirectory ("daily-update-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Location -LiteralPath $repositoryRoot

function Invoke-CheckedPython {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $output = @(& py @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $output | Tee-Object -FilePath $logPath -Append

    if ($exitCode -ne 0) {
        throw "Python command failed with exit code ${exitCode}: py $($Arguments -join ' ')"
    }
}

try {
    "[{0}] daily statistics update started" -f (Get-Date -Format 'o') |
        Tee-Object -FilePath $logPath -Append

    Invoke-CheckedPython -Arguments @('-m', 'collector.dev_stats', 'run')

    $publishArguments = @('-m', 'collector.dev_stats', 'publish')
    if ($DryRun) {
        $publishArguments += '--dry-run'
    }
    Invoke-CheckedPython -Arguments $publishArguments

    "[{0}] daily statistics update completed" -f (Get-Date -Format 'o') |
        Tee-Object -FilePath $logPath -Append
} catch {
    "[{0}] daily statistics update failed: {1}" -f (Get-Date -Format 'o'), $_.Exception.Message |
        Tee-Object -FilePath $logPath -Append
    exit 1
}
