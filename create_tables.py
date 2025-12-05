"""
Create missing tables in database
"""
from app import app, db

def create_tables():
    with app.app_context():
        print("🔧 Creating database tables...")
        db.create_all()
        print("✅ All tables created successfully!")

        # แสดงตารางทั้งหมด
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        print(f"\n📋 Tables in database ({len(tables)}):")
        for table in sorted(tables):
            print(f"  - {table}")

if __name__ == '__main__':
    create_tables()
