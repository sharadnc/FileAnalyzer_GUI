@echo off
rem Read VENV_PYTHON from project .env and export ENV_VENV_PYTHON (resolved path).
rem Usage: call "%ROOT%\scripts\read_venv_from_dotenv.bat" "%ROOT%"

setlocal EnableDelayedExpansion
set "ROOT=%~1"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "ENV_VENV_PYTHON="
set "RAW="

if not exist "%ROOT%\.env" goto :done

for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ROOT%\.env") do (
  if /i "%%~A"=="VENV_PYTHON" if not defined RAW set "RAW=%%~B"
)

if not defined RAW goto :done

set "RAW=!RAW:"=!"
for /f "tokens=* delims= " %%Z in ("!RAW!") do set "RAW=%%Z"
for /f "tokens=* delims= " %%Z in ("!RAW!") do set "RAW=%%Z"

if "!RAW!"=="" goto :done

echo !RAW!| findstr /r "^[A-Za-z]:" >nul 2>&1
if not errorlevel 1 (
  set "ENV_VENV_PYTHON=!RAW!"
) else (
  set "ENV_VENV_PYTHON=%ROOT%\!RAW!"
)

:done
endlocal & set "ENV_VENV_PYTHON=%ENV_VENV_PYTHON%"
exit /b 0
