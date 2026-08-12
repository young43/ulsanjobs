@echo off
REM ---------------------------------------------------------------
REM  Ulsan public-sector job board refresh
REM  NOTE: keep this file ASCII-only. cmd.exe parses .bat with the
REM  OEM codepage, so Korean text here breaks line parsing.
REM  Korean messages are printed by the Python scripts instead.
REM ---------------------------------------------------------------
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set DAYS=%1
if "%DAYS%"=="" set DAYS=60

python collect.py --days %DAYS%
if errorlevel 1 goto failed

python build_site.py
if errorlevel 1 goto failed

if /i "%2"=="noopen" goto done
start "" "%~dp0site\index.html"

:done
endlocal
exit /b 0

:failed
echo.
echo  [FAILED] See the messages above.
endlocal
exit /b 1
