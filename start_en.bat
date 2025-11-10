@echo off
echo ========================================
echo   VNIX Order Management System
echo ========================================
echo.

REM Display IP Address
echo Checking IP Address...
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
    set IP=%%a
    echo Access from this network: http://%%a:8000
)
echo.
echo Or access locally: http://localhost:8000
echo.
echo ========================================
echo Starting system...
echo Press Ctrl+C to stop
echo ========================================
echo.

python app.py
