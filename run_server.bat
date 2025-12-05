@echo off
cd /d "%~dp0"

REM --- สร้าง venv ถ้ายังไม่มี ---
if not exist "venv\Scripts\python.exe" (
  py -3 -m venv venv
)

REM --- เปิด venv แบบชัวร์ (มี .bat) ---
call "%~dp0venv\Scripts\activate.bat"

python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

REM --- ถ้า V2 ยังรันอยู่ ให้ใช้พอร์ตอื่น (เช่น 8001) ---
set PORT=8000

python app.py
pause
