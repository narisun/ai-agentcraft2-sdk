@echo off
rem Builds and installs the SDK: runs tools\install_sdk.py with the Python you select.
rem
rem   install-sdk.bat [--dev] [--editable] [pip options]
rem
rem Contributors use --dev --editable. With no options the packages are built and
rem installed as wheels, as a consumer would get them. The Python is the one named
rem by the PYTHON variable, or "python" on your PATH:
rem
rem   set "PYTHON=C:\path to approved Python\python.exe"
setlocal
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -c "import sys" >nul 2>&1
if errorlevel 1 goto missing
"%PYTHON%" "%~dp0tools\install_sdk.py" %*
exit /b %ERRORLEVEL%

:missing
>&2 echo error: cannot run "%PYTHON%"
>&2 echo fix: install Python 3.11 or newer, or set PYTHON to the full path of your approved Python, then run this again
exit /b 1
