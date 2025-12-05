#!/usr/bin/env python
# migrate_api_configs_simple.py
"""
Simple database migration script to add api_configs table using raw SQL
Run this once to add the new table to your database

Usage:
    python migrate_api_configs_simple.py
"""

import sqlite3
import os

def migrate():
    """Add api_configs table to database using SQL"""
    print("Starting migration: Adding api_configs table...")

    db_path = os.path.join(os.path.dirname(__file__), "data.db")

    if not os.path.exists(db_path):
        print(f"❌ Database not found at: {db_path}")
        return

    print(f"📂 Database: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check if table already exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='api_configs'
        """)

        if cursor.fetchone():
            print("⚠️  api_configs table already exists. Skipping...")
        else:
            # Create api_configs table
            cursor.execute("""
                CREATE TABLE api_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shop_id INTEGER,
                    config_name VARCHAR(128) NOT NULL,
                    platform VARCHAR(64),
                    api_url VARCHAR(512) NOT NULL,
                    data_path VARCHAR(256),
                    api_key TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    created_by_user_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (shop_id) REFERENCES shops(id),
                    FOREIGN KEY (created_by_user_id) REFERENCES users(id)
                )
            """)

            conn.commit()
            print("✅ api_configs table created successfully!")

        # Show table structure
        cursor.execute("PRAGMA table_info(api_configs)")
        columns = cursor.fetchall()

        print("\n📋 Table structure:")
        for col in columns:
            print(f"   - {col[1]}: {col[2]}")

        # Show table count
        cursor.execute("SELECT COUNT(*) FROM api_configs")
        count = cursor.fetchone()[0]
        print(f"\n📊 Current records: {count}")

        print("\n✅ Migration completed successfully!")

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    migrate()
