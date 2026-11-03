param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$listenPort = if ($env:CMASTER_PORT) { [int]$env:CMASTER_PORT } else { 5001 }
if ($listenPort -lt 1 -or $listenPort -gt 65535) { throw 'Invalid CMASTER_PORT.' }
$localUrl = "http://127.0.0.1:$listenPort"
$running = $false
try {
    $response = Invoke-WebRequest -Uri "$localUrl/healthz" -UseBasicParsing -TimeoutSec 2
    $running = $response.Headers['X-CMaster-App'] -eq 'CMaster'
} catch { }
if ($running) {
    Write-Host '[CMaster] Already running.'
    if (-not $NoBrowser) { Start-Process $localUrl }
    exit 0
}
if (Get-NetTCPConnection -LocalPort $listenPort -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $listenPort is used by another service or an older CMaster. Close it, or choose another CMASTER_PORT."
}
$pythonPath = Join-Path $projectRoot 'runtime\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host '[CMaster] Creating the Python environment...'
    python -m venv runtime\venv
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create venv. Install Python 3.9+ and retry.' }
}
& $pythonPath -c "import importlib.util,sys; sys.exit(importlib.util.find_spec('flask') is None)"
if ($LASTEXITCODE -ne 0) {
    & $pythonPath -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and retry.' }
}
foreach ($contentFile in @('courses.json', 'question_bank.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "data\$contentFile"))) { throw "Missing data/$contentFile. Restore the course data first." }
}
& $pythonPath -c "import judge; info=judge.compiler_info(); print(info.get('version') or info.get('hint'))"
$env:CMASTER_PORT = "$listenPort"
if (-not $NoBrowser) {
    Start-Process -FilePath $pythonPath -ArgumentList @(('"' + (Join-Path $PSScriptRoot 'open_when_ready.py') + '"'), "$listenPort") -WindowStyle Hidden
}
Write-Host "[CMaster] Starting at $localUrl. Press Ctrl+C to stop."
& $pythonPath app.py
if ($LASTEXITCODE -ne 0) { throw 'The server stopped with an error. See the message above.' }
