@echo off
echo ========================================
echo   Installing Dependencies
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Please install Python first
    echo Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Python version:
python --version
echo.

REM Install pip packages
echo Installing Python packages...
echo.
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ========================================
echo   Installation Complete!
echo ========================================
echo.
echo You can now run the system by:
echo   1. Double-click start_en.bat
echo   2. Or run: python app.py
echo.
pause
