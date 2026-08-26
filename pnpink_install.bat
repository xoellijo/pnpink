@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "REPOSITORY=xoellijo/pnpink"
set "SCRIPT_NAME=%~nx0"
set "VERSION=latest"
set "SKIP_GHOSTSCRIPT=0"
set "TEMP_DIR=%TEMP%\pnpink_bootstrap_%RANDOM%_%RANDOM%"
set "SEVENZIP_VERSION=26.02"
set "SEVENZIP_FILE_VERSION=2602"
set "GHOSTSCRIPT_VERSION=10.07.1"
set "GHOSTSCRIPT_FILE_VERSION=10071"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--help" goto usage
if /I "%~1"=="-h" goto usage
if /I "%~1"=="--no-ghostscript" (
  set "SKIP_GHOSTSCRIPT=1"
  shift
  goto parse_args
)
if /I "%~1"=="--version" (
  if "%~2"=="" goto usage
  set "VERSION=%~2"
  shift
  shift
  goto parse_args
)
if /I "!VERSION!"=="latest" (
  set "VERSION=%~1"
  shift
  goto parse_args
)
goto usage

:args_done
if /I not "!VERSION!"=="latest" if /I "!VERSION:~0,1!"=="v" set "VERSION=!VERSION:~1!"

echo === PnPInk Installer for Windows ===
echo Version: !VERSION!

where curl.exe >nul 2>nul || (
  echo ERROR: curl.exe is required. Windows 10 and 11 include it by default.
  exit /b 1
)
where tar.exe >nul 2>nul || (
  echo ERROR: tar.exe is required. Windows 10 and 11 include it by default.
  exit /b 1
)

mkdir "!TEMP_DIR!" >nul 2>nul || goto fail
set "INSTALLER=!TEMP_DIR!\install.py"
set "PAYLOAD=!TEMP_DIR!\pnpink_payload.zip"

if /I "!VERSION!"=="latest" (
  set "BASE_URL=https://github.com/!REPOSITORY!/releases/latest/download"
  set "PAYLOAD_NAME=pnpink_payload_latest.zip"
) else (
  set "BASE_URL=https://github.com/!REPOSITORY!/releases/download/v!VERSION!"
  set "PAYLOAD_NAME=pnpink_payload_!VERSION!.zip"
)

call :download "!BASE_URL!/install.py" "!INSTALLER!" || goto fail
call :download "!BASE_URL!/!PAYLOAD_NAME!" "!PAYLOAD!" || goto fail

if "!SKIP_GHOSTSCRIPT!"=="0" call :install_ghostscript || goto fail

call :find_inkscape || goto fail
call :find_inkscape_python || goto fail

echo Using Inkscape Python: !INKSCAPE_PYTHON!
"!INKSCAPE_PYTHON!" "!INSTALLER!" "!PAYLOAD!" --inkscape "!INKSCAPE_EXE!"
set "RESULT=!ERRORLEVEL!"
call :cleanup
if not "!RESULT!"=="0" exit /b !RESULT!
echo.
echo Installation completed successfully.
exit /b 0

:download
echo Downloading %~1
curl.exe -fL --retry 3 --connect-timeout 20 -o "%~2" "%~1"
exit /b %ERRORLEVEL%

:find_inkscape
set "INKSCAPE_EXE="
for /f "delims=" %%P in ('where inkscape.exe 2^>nul') do if not defined INKSCAPE_EXE set "INKSCAPE_EXE=%%P"
if defined INKSCAPE_EXE exit /b 0
for %%P in (
  "%ProgramFiles%\Inkscape\bin\inkscape.exe"
  "%ProgramFiles(x86)%\Inkscape\bin\inkscape.exe"
  "%LocalAppData%\Programs\Inkscape\bin\inkscape.exe"
) do if exist "%%~P" if not defined INKSCAPE_EXE set "INKSCAPE_EXE=%%~P"
if defined INKSCAPE_EXE exit /b 0
echo ERROR: Inkscape was not found. Install or open Inkscape and run this file again.
exit /b 1

:find_inkscape_python
set "INKSCAPE_PYTHON="
for %%D in ("!INKSCAPE_EXE!") do set "INKSCAPE_DIR=%%~dpD"
for %%P in (
  "!INKSCAPE_DIR!python.exe"
  "!INKSCAPE_DIR!python\python.exe"
  "!INKSCAPE_DIR!..\python\python.exe"
) do if exist "%%~P" if not defined INKSCAPE_PYTHON set "INKSCAPE_PYTHON=%%~fP"
if defined INKSCAPE_PYTHON exit /b 0
echo ERROR: Inkscape's bundled Python was not found next to !INKSCAPE_EXE!.
exit /b 1

:install_ghostscript
set "GS_DIR=%LocalAppData%\PnPInk\ghostscript"
set "GS_EXE=!GS_DIR!\bin\gswin64c.exe"
if exist "!GS_EXE!" (
  echo Portable Ghostscript already installed: !GS_EXE!
  exit /b 0
)
for %%G in (gswin64c.exe gswin32c.exe gs.exe) do (
  where %%G >nul 2>nul && (
    echo Ghostscript already available: %%G
    exit /b 0
  )
)
for /d %%D in (
  "%ProgramFiles%\gs\gs*"
  "%ProgramFiles(x86)%\gs\gs*"
) do if exist "%%~D\bin\gswin64c.exe" (
  echo Ghostscript already installed: %%~D\bin\gswin64c.exe
  exit /b 0
)

echo Installing portable Ghostscript !GHOSTSCRIPT_VERSION! into !GS_DIR!
set "SEVEN_SETUP=!TEMP_DIR!\7zip-installer.exe"
set "SEVEN_DIR=!TEMP_DIR!\7zip"
set "GS_INSTALLER=!TEMP_DIR!\ghostscript-installer.7z"
set "GS_EXTRACT=!TEMP_DIR!\ghostscript-extracted"

call :download "https://github.com/ip7z/7zip/releases/download/!SEVENZIP_VERSION!/7z!SEVENZIP_FILE_VERSION!-x64.exe" "!SEVEN_SETUP!" || exit /b 1

mkdir "!SEVEN_DIR!" >nul 2>nul || exit /b 1
tar.exe -xf "!SEVEN_SETUP!" -C "!SEVEN_DIR!" || exit /b 1
set "SEVENZIP=!SEVEN_DIR!\7z.exe"
if not exist "!SEVENZIP!" (
  echo ERROR: 7-Zip command-line executable was not found after extraction.
  exit /b 1
)

call :download "https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/gs!GHOSTSCRIPT_FILE_VERSION!/gs!GHOSTSCRIPT_FILE_VERSION!w64.exe" "!GS_INSTALLER!" || exit /b 1

"!SEVENZIP!" x "!GS_INSTALLER!" -o"!GS_EXTRACT!" -y >nul 2>nul
if not exist "!GS_EXTRACT!\bin\gswin64c.exe" (
  echo ERROR: gswin64c.exe was not found in the extracted Ghostscript installer.
  exit /b 1
)
if exist "!GS_DIR!" rmdir /s /q "!GS_DIR!"
mkdir "!GS_DIR!" >nul 2>nul || exit /b 1
xcopy "!GS_EXTRACT!\*" "!GS_DIR!\" /E /I /Y /Q >nul || exit /b 1
if not exist "!GS_EXE!" (
  echo ERROR: Portable Ghostscript copy failed.
  exit /b 1
)
"!GS_EXE!" -version >nul 2>nul || (
  echo ERROR: Portable Ghostscript could not be executed.
  exit /b 1
)
echo Portable Ghostscript installed: !GS_EXE!
exit /b 0

:cleanup
if exist "!TEMP_DIR!" rmdir /s /q "!TEMP_DIR!"
exit /b 0

:usage
echo Usage: !SCRIPT_NAME! [version] [--no-ghostscript]
echo        !SCRIPT_NAME! --version 0.55 [--no-ghostscript]
exit /b 2

:fail
echo.
echo ERROR: Installation failed.
call :cleanup
exit /b 1
