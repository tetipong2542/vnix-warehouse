@echo off
echo ========================================
echo   Network Diagnostics for VNIX System
echo ========================================
echo.

echo [1] Checking Python and server status...
python --version
echo.

echo [2] Checking if port 8000 is listening...
netstat -an | findstr :8000
echo.

echo [3] Your IP addresses:
ipconfig | findstr /i "IPv4"
echo.

echo [4] Firewall rules for port 8000:
netsh advfirewall firewall show rule name=all | findstr /i "8000"
echo.

echo [5] Testing local access...
echo Trying to connect to localhost:8000...
powershell -Command "try { $response = Invoke-WebRequest -Uri 'http://localhost:8000' -TimeoutSec 5 -UseBasicParsing; Write-Host '[SUCCESS] Server is responding locally' } catch { Write-Host '[ERROR] Server not responding:' $_.Exception.Message }"
echo.

echo ========================================
echo   Diagnostic Complete
echo ========================================
echo.
echo If you see port 8000 in step 2, the server is running
echo If step 5 shows SUCCESS, the server works locally
echo.
echo Next: Test from Mac by opening Terminal and run:
echo   curl http://192.168.1.150:8000
echo.
pause
