@echo off
REM SPDX-License-Identifier: AGPL-3.0-only
REM Copyright (C) 2026 Vaibhav Chhajer
title Scriptavera
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 goto nopython

python -c "import starlette, uvicorn, multipart, requests, pypdfium2, PIL, cryptography, sympy, numpy" >nul 2>nul
if errorlevel 1 goto install
goto run

:install
echo Installing required packages. This happens on the first run only.
python -m pip install -r requirements.txt
if errorlevel 1 goto pipfailed

:run
python app.py
pause
exit /b 0

:nopython
echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
echo and tick "Add Python to PATH" during setup.
pause
exit /b 1

:pipfailed
echo.
echo Package installation failed. Check your internet connection and run this file again.
pause
exit /b 1
