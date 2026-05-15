@echo off
setlocal EnableExtensions

rem =============================================================================
rem Generate sample pipe-delimited data from Excel metadata.
rem Runs: tools\generate_pipe_data.py
rem Default output: sample\<meta_stem>_generated.txt
rem =============================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

cd /d "%ROOT%"

set "PYTHON="
set "SHARED_VENV_PYTHON=G:\My Drive\AI_Projects\.venv\Scripts\python.exe"

if exist "%ROOT%\.venv\Scripts\python.exe" call :try_python "%ROOT%\.venv\Scripts\python.exe"
if not defined PYTHON if exist "%SHARED_VENV_PYTHON%" call :try_python "%SHARED_VENV_PYTHON%"
if not defined PYTHON if exist "%ROOT%\venv\Scripts\python.exe" call :try_python "%ROOT%\venv\Scripts\python.exe"

if not defined PYTHON (
  where py >nul 2>&1
  if not errorlevel 1 call :try_python py -3
)
if not defined PYTHON (
  where python >nul 2>&1
  if not errorlevel 1 call :try_python python
)

if not defined PYTHON (
  echo [ERROR] No Python with pandas found. Install deps: pip install -r requirements.txt
  pause
  exit /b 1
)

echo Running generate_pipe_data.py ...
if "%PYTHON: =%"=="%PYTHON%" (
  "%PYTHON%" "%ROOT%\tools\generate_pipe_data.py" %*
) else (
  %PYTHON% "%ROOT%\tools\generate_pipe_data.py" %*
)
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo [ERROR] generate_pipe_data.py failed with exit code %EXITCODE%.
  pause
  exit /b %EXITCODE%
)

echo.
echo Done.
pause
endlocal
exit /b 0

rem ---------------------------------------------------------------------------
rem Accept full path to python.exe, or launcher tokens (e.g. py -3).
rem ---------------------------------------------------------------------------
:try_python
if "%~1"=="" exit /b 1
"%~1" %2 %3 %4 -c "import pandas" >nul 2>&1
if errorlevel 1 exit /b 1
if "%~2"=="" (
  set "PYTHON=%~1"
) else (
  set "PYTHON=%~1 %~2"
)
exit /b 0
