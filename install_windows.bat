@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo === PnPInk Installer (Windows) ===
cd /d "%~dp0" || (echo ERROR: cannot cd & pause & exit /b 1)

REM =========================================================
REM TEST SWITCH:
REM   set PNPINK_NO_SYSTEM_PY=1  -> skip system python/python3
REM =========================================================
if "%PNPINK_NO_SYSTEM_PY%"=="1" (
  echo [DBG] PNPINK_NO_SYSTEM_PY=1 -> skipping system Python
  goto :find_inkscape_python
)

REM -----------------------------
REM 1) System Python first
REM -----------------------------
where py >nul 2>nul && (
  py -3 -V >nul 2>nul && (
    echo Using Python launcher: py -3
    py -3 install.py
    exit /b %errorlevel%
  )
)

where python >nul 2>nul && (
  python -V >nul 2>nul && (
    echo Using system python
    python install.py
    exit /b %errorlevel%
  ) || (
    echo Found "python" on PATH but it is not executable \(possibly Microsoft Store alias\). Skipping...
  )
)

where python3 >nul 2>nul && (
  python3 -V >nul 2>nul && (
    echo Using system python3
    python3 install.py
    exit /b %errorlevel%
  )
)

:find_inkscape_python

REM -----------------------------
REM 2) Find Inkscape path (process -> PATH -> typical -> wait)
REM -----------------------------
set "INK="

for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-Process inkscape -ErrorAction SilentlyContinue | Select-Object -First 1).Path"`) do (
  if not "%%P"=="" set "INK=%%P"
)

if not defined INK (
  where inkscape.exe >nul 2>nul && for /f "delims=" %%I in ('where inkscape.exe') do (
    set "INK=%%I"
    goto :ink_found
  )
)

:ink_found
if not defined INK (
  for %%I in (
    "%ProgramFiles%\Inkscape\bin\inkscape.exe"
    "%ProgramFiles%\Inkscape\inkscape.exe"
    "%ProgramFiles(x86)%\Inkscape\bin\inkscape.exe"
    "%ProgramFiles(x86)%\Inkscape\inkscape.exe"
    "%LocalAppData%\Programs\Inkscape\bin\inkscape.exe"
    "%LocalAppData%\Programs\Inkscape\inkscape.exe"
  ) do (
    if exist %%~I (set "INK=%%~I" & goto :ink_found2)
  )
)
:ink_found2

if not defined INK (
  echo Inkscape not found. Please open Inkscape now...
  for /l %%N in (1,1,30) do (
    for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-Process inkscape -ErrorAction SilentlyContinue | Select-Object -First 1).Path"`) do (
      if not "%%P"=="" set "INK=%%P"
    )
    if defined INK goto :ink_ok
    timeout /t 1 >nul
  )
)

:ink_ok
if not defined INK (
  echo ERROR: Could not locate Inkscape.
  pause
  exit /b 1
)

echo Found Inkscape: %INK%

REM -----------------------------
REM 3) Derive Python candidates from Inkscape location
REM -----------------------------
for %%A in ("%INK%") do set "INKDIR=%%~dpA"
if "%INKDIR:~-1%"=="\" set "INKDIR=%INKDIR:~0,-1%"

set "CANDS="

REM Common: Inkscape\python\python.exe
set "CANDS=%CANDS% "%INKDIR%\..\python\python.exe""
set "CANDS=%CANDS% "%INKDIR%\python\python.exe""

REM Sometimes: Inkscape\bin\python.exe
set "CANDS=%CANDS% "%INKDIR%\python.exe""

REM Extra typical guesses
set "CANDS=%CANDS% "%ProgramFiles%\Inkscape\python\python.exe""
set "CANDS=%CANDS% "%ProgramFiles(x86)%\Inkscape\python\python.exe""
set "CANDS=%CANDS% "%LocalAppData%\Programs\Inkscape\python\python.exe""

REM -----------------------------
REM 4) Try candidates
REM -----------------------------
for %%P in (%CANDS%) do (
  if exist %%~P (
    echo Using Inkscape bundled python: %%~P
    "%%~P" install.py
    exit /b %errorlevel%
  )
)

echo.
echo ERROR: Python not found (system disabled or missing; Inkscape Python not found).
echo Tip: to use system Python, unset PNPINK_NO_SYSTEM_PY or set it to 0.
pause
exit /b 1
