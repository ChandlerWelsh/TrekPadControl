# Builds dist\TrekPad\TrekPad.exe (a folder app: fast to start, nothing to install).
# Run from the repo root:   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
# Add -Install to copy it to %LOCALAPPDATA%\Programs\TrekPad and create Start Menu and
# desktop shortcuts (close TrekPad first if it's running from there).
param([switch]$Install)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install --quiet --user -r requirements.txt pyinstaller pillow
if (-not (Test-Path assets\trekpad.ico)) { python tools\make_icon.py }

python -m PyInstaller --noconfirm --clean --windowed --name TrekPad `
  --icon assets\trekpad.ico `
  --add-data "trekpad\ui;trekpad\ui" `
  --collect-submodules bleak --collect-submodules winrt `
  --collect-all webview --collect-all pythonnet --collect-all clr_loader `
  TrekPad.pyw
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = (Resolve-Path dist\TrekPad\TrekPad.exe).Path
Write-Host "Built $exe"

if ($Install) {
  $dest = Join-Path $env:LOCALAPPDATA "Programs\TrekPad"
  if (Get-Process TrekPad -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$dest*" }) {
    throw "TrekPad is running from $dest. Close it, then run this again."
  }
  if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
  Copy-Item -Recurse dist\TrekPad $dest
  $exe = Join-Path $dest "TrekPad.exe"
  Write-Host "Installed $exe"
  $shell = New-Object -ComObject WScript.Shell
  foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "TrekPad.lnk"))
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = Split-Path $exe
    $lnk.IconLocation = "$exe,0"
    $lnk.Description = "Control the NovaWalk W50 TrekPad"
    $lnk.Save()
    Write-Host "Shortcut: $(Join-Path $dir 'TrekPad.lnk')"
  }
}
