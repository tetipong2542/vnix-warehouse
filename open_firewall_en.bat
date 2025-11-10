@echo off
echo ========================================
echo   Opening Windows Firewall Port 8000
echo ========================================
echo.
echo This will allow other devices to access the system
echo.

REM Add firewall rule for port 8000
netsh advfirewall firewall add rule name="VNIX Order Management" dir=in action=allow protocol=TCP localport=8000

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to open firewall!
    echo Please run this file as Administrator:
    echo   1. Right-click "open_firewall_en.bat"
    echo   2. Select "Run as administrator"
    echo.
) else (
    echo.
    echo [SUCCESS] Firewall opened successfully!
    echo Port 8000 is now accessible from other devices
    echo.
)

pause
