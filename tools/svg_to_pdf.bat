@echo off
setlocal EnableExtensions

set "PNPINK_INKSCAPE_BIN=%USERPROFILE%\inkscape\bin"
if not exist "%PNPINK_INKSCAPE_BIN%\libcairo-2.dll" (
  echo ERROR: Inkscape Cairo runtime not found at "%PNPINK_INKSCAPE_BIN%".
  exit /b 1
)

set "PATH=%PNPINK_INKSCAPE_BIN%;%PATH%"
set "PY=%APPDATA%\PnPInk\venv\Scripts\python.exe"

if not exist "%PY%" (
  echo ERROR: venv python not found at "%PY%".
  exit /b 1
)

"%PY%" "%~dp0svg_to_pdf.py" %*
exit /b %errorlevel%
