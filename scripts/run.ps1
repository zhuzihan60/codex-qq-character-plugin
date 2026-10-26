param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CharacterArguments
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Missing .venv. Create the environment and install this checkout first.'
}
$env:PYTHONPATH = Join-Path $projectRoot 'src'
Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath -m qq_character @CharacterArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
