@echo off
REM ---------------------------------------------------------------
REM  Wrapper used by the Windows Task Scheduler job.
REM  Runs refresh.bat quietly and appends everything to logs\refresh.log
REM  ASCII-only on purpose (see the note in refresh.bat).
REM ---------------------------------------------------------------
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir "logs"

echo. >> "logs\refresh.log"
echo ===== run started %date% %time% ===== >> "logs\refresh.log"
call "%~dp0refresh.bat" 60 noopen >> "logs\refresh.log" 2>&1
set RC=%errorlevel%
echo ===== run finished %date% %time%  exit=%RC% ===== >> "logs\refresh.log"

endlocal & exit /b %RC%
