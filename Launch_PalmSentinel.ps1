# ---------------------------------------------------------------------------
#  PalmSentinel V2 -- PowerShell launcher.
#
#  Same two paths as Launch_PalmSentinel.bat, and the same refusal to be vague
#  about them: the packaged build needs nothing, the source checkout needs
#  Python plus the packages in requirements.txt, and which one you are about to
#  get is printed before it starts.
#
#      .\Launch_PalmSentinel.ps1
#      .\Launch_PalmSentinel.ps1 --check
# ---------------------------------------------------------------------------

[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$AppName = 'PalmSentinel V2'
$Packaged = Join-Path $PSScriptRoot 'dist\PalmSentinelV2\PalmSentinelV2.exe'
$RequiredModules = @('flask', 'webview', 'bottle', 'cv2', 'numpy', 'PIL')

Write-Host "$AppName launcher" -ForegroundColor Cyan
Write-Host "  directory : $PSScriptRoot"

if (Test-Path $Packaged) {
    Write-Host '  mode      : packaged build (no Python required)' -ForegroundColor Green
    Write-Host "  target    : $Packaged"
    & $Packaged @Arguments
    exit $LASTEXITCODE
}

Write-Host '  mode      : source checkout' -ForegroundColor Yellow
Write-Host '  note      : this path needs Python 3.11+ and the packages in requirements.txt'

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host ''
    Write-Host '[PalmSentinel] ERROR: no "python" on PATH.' -ForegroundColor Red
    Write-Host '  Either install Python 3.11+ and run:  pip install -r requirements.txt'
    Write-Host '  or build the standalone version:      pyinstaller --noconfirm PalmSentinelV2.spec'
    exit 1
}

# Preflight: report exactly which packages are absent, by name.  The package
# names are passed as argv rather than interpolated into the Python source, and
# the probe source itself uses single quotes only -- Windows PowerShell does not
# escape embedded double quotes when handing a string to a native executable, so
# a probe containing them arrives at Python mangled, and a mangled probe would
# then be misreported as "packages missing".
$probe = 'import importlib.util as u, sys; m = [n for n in sys.argv[1:] if u.find_spec(n) is None]; print('',''.join(m) if m else ''none''); sys.exit(1 if m else 0)'
$ErrorActionPreference = 'Continue'   # let the probe's own stderr surface as text, not a throw
$probeOut = & python -c $probe @RequiredModules
$probeExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($probeExit -ne 0) {
    Write-Host ''
    Write-Host "[PalmSentinel] ERROR: Python was found, but it cannot import what this app needs:" -ForegroundColor Red
    Write-Host "    missing: $probeOut"
    Write-Host ''
    Write-Host '  Install them into that interpreter with:'
    Write-Host '      pip install -r requirements.txt'
    Write-Host '  or use the standalone build, which needs nothing installed.'
    exit 1
}

Write-Host '  interpreter: ' -NoNewline
Write-Host (& python -c "import sys; print(sys.executable)")
& python (Join-Path $PSScriptRoot 'desktop_app.py') @Arguments
exit $LASTEXITCODE
