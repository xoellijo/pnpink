@echo off
setlocal EnableExtensions

set "PNPINK_INKSCAPE_BIN=%USERPROFILE%\inkscape\bin"
if not exist "%PNPINK_INKSCAPE_BIN%\libcairo-2.dll" (
  echo ERROR: Inkscape Cairo runtime not found at "%PNPINK_INKSCAPE_BIN%".
  exit /b 1
)

set "PATH=%PNPINK_INKSCAPE_BIN%;%PATH%"
set "PY=%PNPINK_INKSCAPE_BIN%\python.exe"

if not exist "%PY%" (
  echo ERROR: Inkscape python not found at "%PY%".
  exit /b 1
)

"%PY%" "%~dp0svg_to_pdf_rsvg.py" %*
exit /b %errorlevel%
