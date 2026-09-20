$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$candidates = @(
    @{ Exe = 'py'; Args = @('-3.12') },
    @{ Exe = 'python'; Args = @() },
    @{ Exe = $bundledPython; Args = @() }
)

$selected = $null
foreach ($candidate in $candidates) {
    try {
        & $candidate.Exe @($candidate.Args) --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $selected = $candidate
            break
        }
    } catch {
        continue
    }
}
if (-not $selected) {
    throw 'Python 3.12 is required. Install Python 3.12 and rerun setup.ps1.'
}

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $selected.Exe @($selected.Args) -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
}
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install gateway dependencies.' }
Write-Host 'Gateway dependencies are installed.'

