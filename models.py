
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
from utils import TH_TZ

db = SQLAlchemy()

class Shop(db.Model):
    __tablename__ = "shops"
    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(64), index=True)
    name = db.Column(db.String(128), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(TH_TZ))

    __table_args__ = (
        db.UniqueConstraint("platform", "name", name="uq_shops_platform_name"),
    )

class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(64), unique=True, nullable=False)
    brand = db.Column(db.String(120))
    model = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(TH_TZ))

class Stock(db.Model):
    __tablename__ = "stocks"
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(64), nullable=False, index=True)
    qty = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(TH_TZ), onupdate=lambda: datetime.now(TH_TZ))

class Sales(db.Model):
    __tablename__ = "sales"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(128), nullable=False, index=True)
    po_no = db.Column(db.String(128))
    status = db.Column(db.String(64))  # เปิดใบขายครบตามจำนวนแล้ว / ยังไม่มีการเปิดใบขาย
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(TH_TZ))

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), default="user")  # admin/user
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(TH_TZ))

class APIConfig(db.Model):
    __tablename__ = "api_configs"
    id = db.Column(db.Integer, primary_key=True)
    module_type = db.Column(db.String(32), default='orders')  # orders, stock, products, sales
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=True)
    config_name = db.Column(db.String(128), nullable=False)
    platform = db.Column(db.String(64), nullable=True)  # nullable for stock
    api_url = db.Column(db.String(512), nullable=False)
    data_path = db.Column(db.String(256))
    api_key = db.Column(db.Text)  # Will be encrypted
    is_active = db.Column(db.Boolean, default=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(TH_TZ))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(TH_TZ), onupdate=lambda: datetime.now(TH_TZ))

    # Relationships
    shop = db.relationship("Shop", backref="api_configs")
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])

    def __repr__(self):
        return f"<APIConfig {self.config_name}>"

class PrintSetting(db.Model):
    """Model for storing system print settings"""
    __tablename__ = "print_settings"
    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    setting_value = db.Column(db.String(255), nullable=False)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(TH_TZ))
    
    # Relationships
    updated_by = db.relationship("User", foreign_keys=[updated_by_user_id])
    
    def __repr__(self):
        return f"<PrintSetting {self.setting_key}={self.setting_value}>"

class OrderLine(db.Model):
    __tablename__ = "order_lines"
    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(20), nullable=False)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False)
    order_id = db.Column(db.String(128), nullable=False)
    sku = db.Column(db.String(64), nullable=False)
    qty = db.Column(db.Integer, default=1)
    item_name = db.Column(db.String(512))
    order_time = db.Column(db.DateTime)  # tz-aware
    logistic_type = db.Column(db.String(255))
    imported_at = db.Column(db.DateTime, default=lambda: datetime.now(TH_TZ))
    import_date = db.Column(db.Date)  # วันที่นำเข้า (อ้างอิง พ.ศ. ในหน้า UI)
    accepted = db.Column(db.Boolean, default=False)
    accepted_at = db.Column(db.DateTime)
    accepted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    accepted_by_username = db.Column(db.String(64))
    dispatch_round = db.Column(db.Integer)  # จ่ายงาน(รอบที่)
    
    # สถานะการพิมพ์ Warehouse Job Sheet
    printed_warehouse = db.Column(db.Integer, default=0)  # จำนวนครั้งที่พิมพ์
    printed_warehouse_at = db.Column(db.DateTime)  # ครั้งล่าสุดที่พิมพ์
    printed_warehouse_by = db.Column(db.String(64))  # ผู้พิมพ์ครั้งล่าสุด
    
    # สถานะการพิมพ์ Picking List
    printed_picking = db.Column(db.Integer, default=0)  # จำนวนครั้งที่พิมพ์
    printed_picking_at = db.Column(db.DateTime)  # ครั้งล่าสุดที่พิมพ์
    printed_picking_by = db.Column(db.String(64))  # ผู้พิมพ์ครั้งล่าสุด

    __table_args__ = (
        db.UniqueConstraint('platform', 'shop_id', 'order_id', 'sku', name='uq_orderline'),
    )
    
    @property
    def is_printed_warehouse(self):
        """ตรวจสอบว่าพิมพ์ Warehouse แล้วหรือยัง"""
        return self.printed_warehouse and self.printed_warehouse > 0
    
    @property
    def is_printed_picking(self):
        """ตรวจสอบว่าพิมพ์ Picking แล้วหรือยัง"""
        return self.printed_picking and self.printed_picking > 0
