<#
.SYNOPSIS
    Install the equipment-booking service on Windows (PowerShell 5.1+).
.DESCRIPTION
    Same 6 steps as setup.sh: system checks, venv, (no heavy deps), Python
    packages, .env + database, verify_install.py + tests.
    PURE ASCII FILE: PowerShell 5.1 reads scripts in the ANSI code page.
.PARAMETER PythonBin
    Python launcher command, default "py -3.12".
.PARAMETER NoTests
    Skip pytest at the end.
.PARAMETER DebugMode
    Verbose output.
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
.EXAMPLE
    .\setup.ps1 -PythonBin "py -3.13"
#>
param(
    [string]$PythonBin = "py -3.12",
    [switch]$NoTests,
    [switch]$DebugMode
)

$ErrorActionPreference = "Continue"
$script:LogPrefix = "[setup]"
Set-Location $PSScriptRoot

function Write-Log  { param([string]$Msg) Write-Host "$script:LogPrefix $Msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host "$script:LogPrefix [OK] $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "$script:LogPrefix [WARN] $Msg" -ForegroundColor Yellow }
function Write-Err  { param([string]$Msg) Write-Host "$script:LogPrefix [FAIL] $Msg" -ForegroundColor Red }
function Stop-WithError {
    param([string]$Msg)
    Write-Err $Msg
    Write-Err "Re-run with -DebugMode for details; see docs\TROUBLESHOOTING.md."
    exit 1
}
function Confirm-Step {
    param([string]$Question)
    $ans = Read-Host "$script:LogPrefix $Question [y/N]"
    return ($ans -eq "y" -or $ans -eq "Y")
}
function Enter-Utf8Console {
    $saved = @{ Console = $null; Output = $OutputEncoding; PyIo = $env:PYTHONIOENCODING }
    try {
        $saved.Console = [Console]::OutputEncoding
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    } catch { Write-Warn "Cannot switch console output encoding." }
    $global:OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $env:PYTHONIOENCODING = "utf-8"
    return $saved
}
function Exit-Utf8Console {
    param($Saved)
    if ($null -eq $Saved) { return }
    if ($null -ne $Saved.Console) { try { [Console]::OutputEncoding = $Saved.Console } catch { } }
    $global:OutputEncoding = $Saved.Output
    if ($null -eq $Saved.PyIo) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $Saved.PyIo }
}
function Clear-InternetMark {
    param([string]$Root)
    $marked = Get-ChildItem -LiteralPath $Root -Recurse -File | Where-Object {
        $null -ne (Get-Item -LiteralPath $_.FullName -Stream Zone.Identifier -ErrorAction SilentlyContinue) }
    if ($marked) {
        Write-Log ("Removing 'downloaded from Internet' mark from {0} file(s)" -f @($marked).Count)
        $marked | Unblock-File
    }
}

if ($DebugMode) { Set-PSDebug -Trace 1 }
$N = 6
$saved = Enter-Utf8Console
try {
    Clear-InternetMark $PSScriptRoot

    # ------------------------------------------------------------ 1
    Write-Log "Step 1/$N`: system checks"
    $parts = $PythonBin.Split(" ")
    $exe = $parts[0]
    $pyArgs = @()
    if ($parts.Length -gt 1) { $pyArgs = $parts[1..($parts.Length - 1)] }
    $ver = & $exe @pyArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $ver) {
        Stop-WithError "Python not found via '$PythonBin'. Install: winget install -e --id Python.Python.3.12"
    }
    # SQLAlchemy 2.1 needs >= 3.11, aiogram 3.31 needs < 3.15
    & $exe @pyArgs -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,15) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Python $ver is outside 3.11-3.14 (3.12 recommended)."
        if (-not (Confirm-Step "Continue anyway?")) { Stop-WithError "Install Python 3.12 and re-run." }
    }
    if ($env:CONDA_DEFAULT_ENV) {
        Write-Warn "Conda environment '$env:CONDA_DEFAULT_ENV' is active."
        if (-not (Confirm-Step "Continue?")) { Stop-WithError "Run 'conda deactivate' and start again." }
    }
    Write-Ok "Step 1/$N passed (Python $ver)"

    # ------------------------------------------------------------ 2
    Write-Log "Step 2/$N`: virtual environment .venv"
    $py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        & $exe @pyArgs -m venv .venv
        if ($LASTEXITCODE -ne 0) { Stop-WithError "venv creation failed" }
    }
    & $py -m pip install --upgrade pip -q
    if ($LASTEXITCODE -ne 0) { Stop-WithError "pip upgrade failed (no internet? see REPRODUCIBILITY.md)" }
    Write-Ok "Step 2/$N passed"

    # ------------------------------------------------------------ 3
    Write-Log "Step 3/$N`: heavy dependencies -- none for this project"
    Write-Ok "Step 3/$N passed"

    # ------------------------------------------------------------ 4
    Write-Log "Step 4/$N`: Python packages"
    $lock = "requirements-lock-win_amd64.txt"
    if ([Environment]::Is64BitOperatingSystem -and (Test-Path $lock) -and $ver -eq "3.12") {
        & $py -m pip install --require-hashes -r $lock -q
    } else {
        & $py -m pip install -r requirements.txt -q
    }
    if ($LASTEXITCODE -ne 0) { Stop-WithError "pip install failed" }
    & $py -m pip freeze | Out-File -Encoding ascii requirements-frozen.txt
    Write-Ok "Step 4/$N passed"

    # ------------------------------------------------------------ 5
    Write-Log "Step 5/$N`: .env and database"
    if (-not (Test-Path ".env")) {
        $secret = & $py -c "import secrets; print(secrets.token_urlsafe(32))"
        $text = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot ".env.example"), [System.Text.Encoding]::UTF8)
        $text = $text -replace "(?m)^WEB_SECRET_KEY=.*$", "WEB_SECRET_KEY=$secret"
        [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), $text, (New-Object System.Text.UTF8Encoding($false)))
        Write-Warn ".env created from .env.example -- fill BOT_TOKEN and both WEB_*_PASSWORD (open in Notepad)"
    }
    & $py main.py init-db
    if ($LASTEXITCODE -ne 0) { Stop-WithError "database initialisation failed" }
    Write-Ok "Step 5/$N passed"

    # ------------------------------------------------------------ 6
    Write-Log "Step 6/$N`: verification"
    & $py verify_install.py
    if ($LASTEXITCODE -ne 0) { Stop-WithError "verify_install.py reported failures" }
    if (-not $NoTests) {
        & $py -m pytest -q
        if ($LASTEXITCODE -ne 0) { Stop-WithError "tests failed" }
    }
    Write-Ok "Step 6/$N passed"
    Write-Ok "Done. Next: edit .env, then run:  .venv\Scripts\python.exe main.py run"
}
finally {
    Exit-Utf8Console $saved
    if ($DebugMode) { Set-PSDebug -Off }
}
