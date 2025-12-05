"""
Migration: Add module_type column to api_configs table
เพื่อแยก Config ระหว่าง orders, stock, products, sales
"""

import sqlite3
import os

def migrate():
    db_path = 'app.db'

    if not os.path.exists(db_path):
        print(f"❌ Database not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # ตรวจสอบว่ามี column module_type หรือยัง
        cursor.execute("PRAGMA table_info(api_configs)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'module_type' in columns:
            print("✅ Column 'module_type' already exists")
            return

        print("🔧 Adding 'module_type' column to api_configs...")

        # เพิ่ม column module_type
        cursor.execute("""
            ALTER TABLE api_configs
            ADD COLUMN module_type VARCHAR(32) DEFAULT 'orders'
        """)

        # Update existing configs to 'orders' (default behavior)
        cursor.execute("""
            UPDATE api_configs
            SET module_type = 'orders'
            WHERE module_type IS NULL
        """)

        conn.commit()
        print("✅ Successfully added 'module_type' column")
        print("✅ All existing configs set to 'orders'")

        # แสดงข้อมูล
        cursor.execute("SELECT id, config_name, module_type FROM api_configs")
        rows = cursor.fetchall()

        if rows:
            print(f"\n📋 Current configs ({len(rows)}):")
            for row in rows:
                print(f"  ID {row[0]}: {row[1]} ({row[2]})")
        else:
            print("\n📋 No configs found")

    except Exception as e:
        conn.rollback()
        print(f"❌ Migration failed: {e}")
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    migrate()
