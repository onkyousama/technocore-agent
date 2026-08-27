@echo off
REM roomwatch-onkyou -- daily run wrapper for Windows Task Scheduler.
REM Self-contained: discovers pythonw at run time, so no non-ASCII path is
REM ever written into this file.

setlocal enableextensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PROJECT=%~dp0.."
set "LOGDIR=%USERPROFILE%\.technocore\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
cd /d "%PROJECT%"

set "PYW="
for /f "delims=" %%i in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%i"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
if not defined PYW for /f "delims=" %%i in ('where python.exe 2^>nul') do if not defined PYW set "PYW=%%i"
if not defined PYW set "PYW=python.exe"

echo [%DATE% %TIME%] starting with "%PYW%" >> "%LOGDIR%\task_wrapper.log"
"%PYW%" -m roomwatch daily >> "%LOGDIR%\task_stdout.log" 2>&1
echo [%DATE% %TIME%] exit %ERRORLEVEL% >> "%LOGDIR%\task_wrapper.log"

endlocal
