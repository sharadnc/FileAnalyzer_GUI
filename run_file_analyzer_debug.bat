@echo off
setlocal EnableExtensions
title File Analyzer (debug console)

rem =============================================================================
rem Runs the GUI with python.exe and keeps this window open on errors.
rem Use this when run_file_analyzer.bat closes with no visible message.
rem =============================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

call "%ROOT%\scripts\read_venv_from_dotenv.bat" "%ROOT%"

set "PYTHON="
if defined ENV_VENV_PYTHON call :try "%ENV_VENV_PYTHON%"
if defined PYTHON goto :run
call :try "%ROOT%\.venv\Scripts\python.exe"
if defined PYTHON goto :run
call :try "%ROOT%\venv\Scripts\python.exe"
if defined PYTHON goto :run
call :try "%ROOT%\..\venv\Scripts\python.exe"
if defined PYTHON goto :run

where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; import PyQt5.QtCore" >nul 2>&1
  if not errorlevel 1 (
    echo Using: py -3
    py -3 -u "%ROOT%\src\main.py"
    echo.
    echo Process exit code: %ERRORLEVEL%
    goto :end
  )
)

where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys; import PyQt5.QtCore" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON=python"
    goto :run
  )
)

echo [ERROR] No interpreter with PyQt5 found. Install:
echo   pip install "file-analyzer[ui]"
goto :end

:run
call :resolvew "%PYTHON%"
echo.
echo Launching with:
echo   %PYTHONW_RUN%
echo   working dir: %ROOT%
echo.
"%PYTHONW_RUN%" -u "%ROOT%\src\main.py"
echo.
echo Process exit code: %ERRORLEVEL%

:end
echo.
pause
endlocal
exit /b 0

:try
set "PYTHON="
if not exist "%~1" exit /b 1
"%~1" -c "import sys; import PyQt5.QtCore" >nul 2>&1
if errorlevel 1 exit /b 1
set "PYTHON=%~1"
exit /b 0

:resolvew
set "PYTHONW_RUN=%~dp1pythonw.exe"
if exist "%PYTHONW_RUN%" exit /b 0
set "PYTHONW_RUN=%~1"
exit /b 0
