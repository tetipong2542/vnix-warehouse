"""Check api_configs table schema"""
import sqlite3

conn = sqlite3.connect('app.db')
cursor = conn.cursor()

print("📋 api_configs table schema:")
cursor.execute("PRAGMA table_info(api_configs)")
columns = cursor.fetchall()

for col in columns:
    col_id, name, type_, notnull, default, pk = col
    print(f"  [{col_id}] {name:20s} {type_:15s} {'NOT NULL' if notnull else 'NULL':8s} DEFAULT={default}")

print(f"\nTotal columns: {len(columns)}")

# Check if module_type exists
column_names = [col[1] for col in columns]
if 'module_type' in column_names:
    print("\n✅ module_type column EXISTS!")
else:
    print("\n❌ module_type column MISSING!")

conn.close()
