#!/usr/bin/env python
# migrate_add_api_configs.py
"""
Database migration script to add api_configs table
Run this once to add the new table to your database

Usage:
    python migrate_add_api_configs.py
"""

from app import create_app
from models import db, APIConfig

def migrate():
    """Add api_configs table to database"""
    print("Starting migration: Adding api_configs table...")

    app = create_app()

    with app.app_context():
        try:
            # Create the api_configs table
            db.create_all()
            print("✅ Migration successful!")
            print("   - api_configs table created")

            # Check if table exists
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()

            if 'api_configs' in tables:
                print("✅ Verified: api_configs table exists in database")

                # Show table structure
                columns = inspector.get_columns('api_configs')
                print("\n📋 Table structure:")
                for col in columns:
                    print(f"   - {col['name']}: {col['type']}")
            else:
                print("⚠️  Warning: api_configs table not found in database")

        except Exception as e:
            print(f"❌ Migration failed: {e}")
            raise

if __name__ == '__main__':
    migrate()
