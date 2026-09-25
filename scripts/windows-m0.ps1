param(
    [string]$Output = "artifacts/m0/windows-smoke.json"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv is required. Install uv, then rerun this project-local script."
    }
    & uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw "uv sync --locked failed ($LASTEXITCODE)." }
    & uv run --locked python scripts/m0_smoke.py --output $Output
    if ($LASTEXITCODE -ne 0) { throw "M0 smoke is FAILED or PARTIAL ($LASTEXITCODE). See the evidence JSON and rerun uv run pytest -q locally." }
} finally {
    Pop-Location
}
