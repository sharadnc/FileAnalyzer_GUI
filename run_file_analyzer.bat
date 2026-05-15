@echo off
setlocal EnableExtensions

rem =============================================================================
rem File Analyzer - launch the PyQt5 GUI (no console left open).
rem
rem Uses scripts\launch_gui_hidden.vbs (WScript.Shell.Run, hidden window).
rem
rem Search order (first working interpreter wins):
rem   1) VENV_PYTHON from project .env
rem   2) This folder's .venv
rem   3) venv\ and ..\venv\
rem   4) py -3w (only if that windowed launcher passes the PyQt5 import test)
rem   5) python on PATH
rem
rem If the window still fails: double-click run_file_analyzer_debug.bat to see
rem errors in a console, or read the MessageBox from main.py on failure.
rem =============================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

cd /d "%ROOT%"

call "%ROOT%\scripts\read_venv_from_dotenv.bat" "%ROOT%"

set "LAUNCH_VBS=%ROOT%\scripts\launch_gui_hidden.vbs"
if not exist "%LAUNCH_VBS%" (
  echo [ERROR] Missing launcher script:
  echo   %LAUNCH_VBS%
  pause
  exit /b 1
)

set "PYTHON="
set "USE_PY=0"

if defined ENV_VENV_PYTHON call :try_gui_python "%ENV_VENV_PYTHON%"
if defined PYTHON goto :run_app

call :try_gui_python "%ROOT%\.venv\Scripts\python.exe"
if defined PYTHON goto :run_app

call :try_gui_python "%ROOT%\venv\Scripts\python.exe"
if defined PYTHON goto :run_app

call :try_gui_python "%ROOT%\..\venv\Scripts\python.exe"
if defined PYTHON goto :run_app

where py >nul 2>&1
if not errorlevel 1 (
  py -3w -c "import sys; import PyQt5.QtCore" >nul 2>&1
  if not errorlevel 1 (
    set "USE_PY=1"
    goto :run_app
  )
)

where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys; import PyQt5.QtCore" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON=python"
    goto :run_app
  )
)

echo [ERROR] No working Python interpreter found with PyQt5.
echo Install UI deps in your venv from this folder:
echo   pip install -r requirements.txt
echo or:
echo   pip install "file-analyzer[ui]"
echo.
echo Then try: run_file_analyzer_debug.bat
pause
exit /b 1

:run_app
if "%USE_PY%"=="1" goto :run_hidden_py

call :resolve_pythonw "%PYTHON%"
wscript //nologo "%LAUNCH_VBS%" "%PYTHONW_RUN%" "%ROOT%" "%ROOT%\src\main.py"
goto :done_run

:run_hidden_py
wscript //nologo "%LAUNCH_VBS%" "PY" "%ROOT%" "%ROOT%\src\main.py"

:done_run
endlocal
exit /b 0

rem ---------------------------------------------------------------------------
rem Test the same executable we will use for a windowed launch (pythonw if present).
rem ---------------------------------------------------------------------------
:try_gui_python
set "PYTHON="
if not exist "%~1" exit /b 1
"%~1" -c "import sys; import PyQt5.QtCore" >nul 2>&1
if errorlevel 1 exit /b 1
set "PYW=%~dp1pythonw.exe"
if exist "%PYW%" (
  "%PYW%" -c "import sys; import PyQt5.QtCore" >nul 2>&1
  if errorlevel 1 exit /b 1
)
set "PYTHON=%~1"
exit /b 0

rem ---------------------------------------------------------------------------
:resolve_pythonw
set "PYTHONW_RUN=%~dp1pythonw.exe"
if exist "%PYTHONW_RUN%" exit /b 0
set "PYTHONW_RUN=%~1"
exit /b 0
