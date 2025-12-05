"""Drop and recreate api_configs table"""
import sqlite3

conn = sqlite3.connect('app.db')
cursor = conn.cursor()

print("🗑️  Dropping old api_configs table...")
try:
    cursor.execute("DROP TABLE IF EXISTS api_configs")
    print("✅ Dropped successfully")
except Exception as e:
    print(f"Error dropping: {e}")

print("\n🔧 Creating new api_configs table with module_type...")

cursor.execute("""
CREATE TABLE api_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_type VARCHAR(32) DEFAULT 'orders',
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
print("✅ Created successfully!")

# Verify
cursor.execute("PRAGMA table_info(api_configs)")
columns = cursor.fetchall()

print(f"\n📋 New schema ({len(columns)} columns):")
for col in columns:
    print(f"  [{col[0]}] {col[1]:20s} {col[2]:15s}")

conn.close()

print("\n✅ Recreation completed!")
