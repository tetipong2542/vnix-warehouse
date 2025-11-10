# คำแนะนำการ Deploy บน Railway

## ขั้นตอนการ Deploy

### 1. สร้างบัญชี Railway
- ไปที่ https://railway.app
- สร้างบัญชีใหม่หรือเข้าสู่ระบบด้วย GitHub

### 2. สร้างโปรเจกต์ใหม่
- คลิก "New Project"
- เลือก "Deploy from GitHub repo"
- เลือก repository: `tetipong2542/vnix-warehouse`

### 3. ตั้งค่า Environment Variables (ถ้าจำเป็น)
Railway จะอ่าน PORT อัตโนมัติ แต่คุณสามารถตั้งค่าเพิ่มเติมได้:
- `SECRET_KEY`: Secret key สำหรับ Flask session (ถ้าไม่ตั้งจะใช้ค่า default)
- `APP_NAME`: ชื่อแอปพลิเคชัน (ถ้าไม่ตั้งจะใช้ "VNIX Order Management")

### 4. Railway จะ Deploy อัตโนมัติ
- Railway จะ detect Python project อัตโนมัติ
- อ่าน `requirements.txt` และติดตั้ง dependencies
- ใช้ `Procfile` เพื่อรันแอปพลิเคชัน
- ใช้ `runtime.txt` เพื่อกำหนด Python version

### 5. ตรวจสอบการ Deploy
- ไปที่ tab "Deployments" เพื่อดูสถานะ
- ไปที่ tab "Settings" > "Networking" เพื่อดู URL ที่ Railway สร้างให้

## หมายเหตุสำคัญ

1. **Database**: แอปพลิเคชันใช้ SQLite (`data.db`) ซึ่งจะถูกสร้างใหม่ทุกครั้งที่ deploy ใหม่ หากต้องการเก็บข้อมูลถาวร ควรเปลี่ยนไปใช้ PostgreSQL หรือ MySQL

2. **Static Files**: ไฟล์ static และ templates ถูก push ขึ้นไปแล้ว

3. **Port**: แอปพลิเคชันจะอ่าน PORT จาก environment variable ที่ Railway ตั้งให้อัตโนมัติ

## การอัปเดต
เมื่อ push ไฟล์ใหม่ไปยัง GitHub repository, Railway จะ deploy อัตโนมัติ

