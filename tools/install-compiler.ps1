param([string]$Proxy)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot 'runtime'
$compilerPath = Join-Path $runtimeDir 'w64devkit\bin\gcc.exe'
if (Test-Path -LiteralPath $compilerPath) {
    & $compilerPath --version
    if ($LASTEXITCODE -eq 0) { Write-Host '[CMaster] Compiler is ready.'; exit 0 }
    throw 'Existing compiler is broken. Rename runtime/w64devkit before reinstalling.'
}
if (-not [Environment]::Is64BitOperatingSystem) { throw 'This installer requires 64-bit Windows.' }
# Pinned official release; verify its published SHA-256 before executing the extractor.
$archiveName = 'w64devkit-x64-2.11.0.7z.exe'
$expectedHash = '54c2bb9962e8a30c8270e34c5efc3d5fee579f53087fa7d435b95fbba2af7b2b'
$archivePath = Join-Path $runtimeDir $archiveName
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $archivePath)) {
    $download = @{Uri="https://github.com/skeeto/w64devkit/releases/download/v2.11.0/$archiveName"; OutFile=$archivePath; UseBasicParsing=$true; TimeoutSec=180}
    if ($Proxy) { $download.Proxy = $Proxy }
    Write-Host '[CMaster] Downloading the official portable compiler...'
    try { Invoke-WebRequest @download } catch {
        if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath }
        throw
    }
}
if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    Remove-Item -LiteralPath $archivePath
    throw 'Archive verification failed. Please retry the download.'
}
Write-Host '[CMaster] Verified. Extracting into runtime/w64devkit...'
$extractor = Start-Process -FilePath $archivePath -ArgumentList @('-y', ('-o"' + $runtimeDir + '"')) -WindowStyle Hidden -PassThru -Wait
if ($extractor.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $compilerPath)) { throw 'Extraction failed.' }
& $compilerPath --version
if ($LASTEXITCODE -ne 0) { throw 'Compiler cannot start.' }
Write-Host '[CMaster] Ready. Click Recheck on the home page, or run start.cmd.'
