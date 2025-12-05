"""
Fix: Add module_type column to existing api_configs table
"""
import sqlite3

def fix_database():
    print("🔧 Fixing database - Adding module_type column...")

    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()

    try:
        # ตรวจสอบว่ามี column module_type หรือยัง
        cursor.execute("PRAGMA table_info(api_configs)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'module_type' in columns:
            print("✅ Column 'module_type' already exists!")
            return

        print("Adding column 'module_type'...")

        # เพิ่ม column module_type
        cursor.execute("""
            ALTER TABLE api_configs
            ADD COLUMN module_type VARCHAR(32) DEFAULT 'orders'
        """)

        # Set default value สำหรับ records เก่า
        cursor.execute("""
            UPDATE api_configs
            SET module_type = 'orders'
            WHERE module_type IS NULL
        """)

        conn.commit()

        print("✅ Successfully added 'module_type' column!")

        # ตรวจสอบผลลัพธ์
        cursor.execute("PRAGMA table_info(api_configs)")
        print("\n📋 Current api_configs schema:")
        for col in cursor.fetchall():
            print(f"  {col[1]} ({col[2]})")

        # แสดงข้อมูล configs ที่มี
        cursor.execute("SELECT id, module_type, config_name FROM api_configs")
        configs = cursor.fetchall()

        if configs:
            print(f"\n📦 Found {len(configs)} config(s):")
            for cfg in configs:
                print(f"  ID {cfg[0]}: {cfg[2]} (module: {cfg[1]})")
        else:
            print("\n📦 No configs found in database")

    except sqlite3.OperationalError as e:
        print(f"❌ Error: {e}")
        if "duplicate column name" in str(e):
            print("   Column already exists, this is OK!")
        else:
            raise
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    fix_database()
    print("\n✅ Migration completed!")
