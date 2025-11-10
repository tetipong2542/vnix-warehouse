
# importers.py
from __future__ import annotations

import pandas as pd
from datetime import datetime, date
from flask import flash
from sqlalchemy.exc import IntegrityError

from utils import parse_datetime_guess, normalize_platform, TH_TZ
from models import db, Shop, Product, Stock, Sales, OrderLine

# ===== Column dictionaries =====
COMMON_ORDER_ID   = ["orderNumber","Order Number","order_id","Order ID","order_sn","Order No","เลข Order","No.","OrderNo"]
COMMON_SKU        = ["sellerSku","Seller SKU","SKU","Sku","Item SKU","SKU Reference No.","รหัสสินค้า"]
COMMON_ITEM_NAME  = ["itemName","Item Name","Product Name","ชื่อสินค้า","ชื่อรุ่น","title","name"]
COMMON_QTY        = ["quantity","Quantity","Qty","จำนวน","จำนวนที่สั่ง","Purchased Qty","Order Item Qty"]
COMMON_ORDER_TIME = ["createdAt","create_time","created_time","Order Time","OrderDate","Order Date","วันที่สั่งซื้อ","Paid Time","paid_time","Created Time","createTime","Created Time"]
COMMON_LOGISTICS  = ["logistic_type","Logistics Service","Shipping Provider","ประเภทขนส่ง","Shipment Method","Delivery Type"]

# >>> ขยายตัวเลือกหัวคอลัมน์สต็อก (กันเคสหลากหลาย/ภาษาไทย-อังกฤษ)
COMMON_STOCK_SKU  = [
    "รหัสสินค้า","SKU","sku","รหัส","รหัส สินค้า","รหัสสินค้า*",
    "รหัสสินค้า Sabuy Soft","SKU Reference No.","รหัส/sku","รหัสสินค้า/sku"
]
COMMON_STOCK_QTY  = [
    "คงเหลือ","Stock","stock","Available","จำนวน","Qty","QTY","STOCK","ปัจจุบัน",
    "ยอดคงเหลือ","จำนวนคงเหลือ","คงเหลือในสต๊อก"
]

COMMON_PRODUCT_SKU   = ["รหัสสินค้า","SKU","sku"]
COMMON_PRODUCT_BRAND = ["Brand","แบรนด์"]
COMMON_PRODUCT_MODEL = ["ชื่อสินค้า","รุ่น","Model","Product"]

# ===== helpers =====
def first_existing(df: pd.DataFrame, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    # fuzzy contains (lower)
    lower_cols = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.lower()
        for col_lower, original in lower_cols.items():
            if key == col_lower or key in col_lower:
                return original
    return None

def get_or_create_shop(platform, shop_name):
    platform = normalize_platform(platform)
    shop = Shop.query.filter_by(name=shop_name).first()
    if not shop:
        shop = Shop(platform=platform, name=shop_name)
        db.session.add(shop)
        db.session.commit()
    return shop

# ===== Importers =====
def import_products(df: pd.DataFrame) -> int:
    sku_col   = first_existing(df, COMMON_PRODUCT_SKU)   or "รหัสสินค้า"
    brand_col = first_existing(df, COMMON_PRODUCT_BRAND) or "Brand"
    model_col = first_existing(df, COMMON_PRODUCT_MODEL) or "ชื่อสินค้า"
    cnt = 0
    for _, row in df.iterrows():
        sku = str(row.get(sku_col, "")).strip()
        if not sku:
            continue
        prod = Product.query.filter_by(sku=sku).first()
        if not prod:
            prod = Product(sku=sku)
        prod.brand = str(row.get(brand_col, "")).strip()
        prod.model = str(row.get(model_col, "")).strip()
        db.session.add(prod); cnt += 1
    db.session.commit()
    return cnt

# >>> ฟังก์ชันนี้ถูกแพตช์ใหม่ให้ทน NaN/หัวคอลัมน์หลายแบบ
def import_stock(df: pd.DataFrame) -> int:
    """
    นำเข้าสต็อกจาก DataFrame:
    - รองรับหัวคอลัมน์หลายแบบ (ไทย/อังกฤษ)
    - Qty ว่าง/NaN จะถูกมองเป็น 0 (กัน error: cannot convert float NaN to integer)
    - รวมยอดเมื่อไฟล์มี SKU ซ้ำหลายบรรทัด
    - อัปเดตทั้งตาราง Stock และ (ถ้ามีคอลัมน์) Product.stock_qty
    คืนค่าจำนวนแถวที่บันทึก (insert/update)
    """
    sku_col = first_existing(df, COMMON_STOCK_SKU)
    qty_col = first_existing(df, COMMON_STOCK_QTY)
    if not sku_col:
        raise ValueError("ไม่พบคอลัมน์ SKU/รหัสสินค้า ในไฟล์สต็อก")
    if not qty_col:
        raise ValueError("ไม่พบคอลัมน์ คงเหลือ/Qty/Stock ในไฟล์สต็อก")

    # ตั้งชื่อมาตรฐาน
    df = df.rename(columns={sku_col: "sku", qty_col: "qty"})

    # ทำความสะอาด
    df["sku"] = df["sku"].astype(str).fillna("").map(lambda x: x.strip())
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0).astype(int)

    # คัดแถวที่ไม่มี SKU
    df = df[df["sku"] != ""]
    if df.empty:
        return 0

    # รวมยอดตาม SKU (กันไฟล์ซ้ำแถว)
    agg = df.groupby("sku", as_index=False)["qty"].sum()

    # อัปเดตฐานข้อมูล
    saved = 0
    for _, row in agg.iterrows():
        sku = row["sku"]
        qty = int(row["qty"] or 0)

        st = Stock.query.filter_by(sku=sku).first()
        if not st:
            st = Stock(sku=sku, qty=qty)
            db.session.add(st)
        else:
            st.qty = qty

        # ถ้ามีฟิลด์ product.stock_qty ให้ sync ด้วย
        prod = Product.query.filter_by(sku=sku).first()
        if prod is not None and hasattr(prod, "stock_qty"):
            try:
                prod.stock_qty = qty
            except Exception:
                # กันชนิดคอลัมน์ไม่ใช่ int
                pass

        saved += 1

    db.session.commit()
    return saved

def import_sales(df: pd.DataFrame) -> int:
    order_col  = first_existing(df, ["เลข Order","Order ID","order_id","orderNumber","Order Number"]) or "เลข Order"
    po_col     = first_existing(df, ["เลขที่ PO","PO","เอกสาร","Document No","เลขที่เอกสาร"])
    status_col = first_existing(df, ["สถานะ","Status"])
    cnt = 0
    for _, row in df.iterrows():
        oid = str(row.get(order_col, "")).strip()
        if not oid:
            continue
        sale = Sales.query.filter_by(order_id=oid).first()
        if not sale:
            sale = Sales(order_id=oid)
        sale.po_no = str(row.get(po_col, "") or "").strip() if po_col else None
        sale.status = str(row.get(status_col, "") or "").strip() if status_col else None
        db.session.add(sale); cnt += 1
    db.session.commit()
    return cnt

# ============================
# INSERT-ONLY ORDER IMPORTER
# ============================
def import_orders(df: pd.DataFrame, platform: str, shop_name: str, import_date: date) -> tuple[int, int]:
    """
    เพิ่มเฉพาะรายการที่ 'ยังไม่เคยมี' ในระบบ
    key ความซ้ำ = (order_id + sku) ตามที่ระบุ
    - ถ้าซ้ำ: ข้าม (ไม่นับเพิ่ม, ไม่นับอัปเดต)
    - เก็บวันที่ import เดิมไว้เพื่อ flash เตือนรวมท้ายฟังก์ชัน
    """
    shop = get_or_create_shop(platform, shop_name)
    platform_std = normalize_platform(platform)

    # ค้นหาชื่อคอลัมน์จากไฟล์ที่อาจต่างแพลตฟอร์ม
    order_col = first_existing(df, COMMON_ORDER_ID)
    sku_col   = first_existing(df, COMMON_SKU)
    name_col  = first_existing(df, COMMON_ITEM_NAME)
    qty_col   = first_existing(df, COMMON_QTY)
    time_col  = first_existing(df, COMMON_ORDER_TIME)
    logi_col  = first_existing(df, COMMON_LOGISTICS)

    if not order_col or not sku_col:
        raise ValueError("ไม่พบคอลัมน์ Order ID หรือ SKU ในไฟล์")

    # รวมรายการซ้ำภายในไฟล์เดียวกันก่อน (ตาม key: order_id+sku) เพื่อให้ qty รวมกัน
    grouped: dict[tuple[str, str], dict] = {}
    for _, row in df.iterrows():
        oid = str(row.get(order_col, "")).strip()
        sku = str(row.get(sku_col, "")).strip()
        if not oid or not sku:
            continue

        # qty fallback เป็น 1 ถ้าไม่มีข้อมูล
        raw_qty = row.get(qty_col, None) if qty_col else None
        if raw_qty is None or (isinstance(raw_qty, float) and pd.isna(raw_qty)):
            qty = 1
        else:
            try:
                qty = int(float(raw_qty))
            except Exception:
                qty = 1

        key = (oid, sku)
        rec = grouped.get(key, {
            "qty": 0,
            "name": str(row.get(name_col, "") or ""),
            "time": row.get(time_col) if time_col else None,
            "logi": str(row.get(logi_col, "") or "") if logi_col else "",
        })
        rec["qty"] += max(qty, 0)
        # เก็บชื่อสินค้าล่าสุดถ้าอันเดิมว่าง
        if not rec.get("name"):
            rec["name"] = str(row.get(name_col, "") or "")
        # เวลา/โลจิสติกส์ เก็บอันล่าสุดที่เจอ
        rec["time"] = row.get(time_col) if time_col else rec.get("time")
        rec["logi"] = str(row.get(logi_col, "") or "") if logi_col else rec.get("logi", "")
        grouped[key] = rec

    added = 0
    updated = 0  # ต้องคงเป็น 0 ตาม requirement
    imported_dates: set[date] = set()

    # เตรียมค้นหา Product (optional) — ไม่บังคับ แต่ช่วยเติมข้อมูล
    # ถ้าโครงสร้าง OrderLine ไม่มี product_id ก็ข้ามได้
    has_product_fk = hasattr(OrderLine, "product_id")

    for (oid, sku), rec in grouped.items():
        # === ตรวจซ้ำ: key = (order_id + sku) ตามข้อกำหนด ===
        exists = OrderLine.query.filter_by(order_id=oid, sku=sku).first()
        if exists:
            # เก็บวันที่เคยนำเข้าไว้เตือน (ถ้ามี)
            if hasattr(exists, "import_date") and isinstance(exists.import_date, date):
                imported_dates.add(exists.import_date)
            # ข้ามรายการนี้ (ไม่เพิ่ม, ไม่อัปเดต)
            continue

        # === สร้างใหม่เท่านั้น (INSERT-ONLY) ===
        order_time = parse_datetime_guess(rec.get("time")) if rec.get("time") is not None else None

        ol_kwargs = dict(
            platform=platform_std,
            shop_id=shop.id,
            order_id=oid,
            sku=sku,
            qty=rec.get("qty", 0) or 0,
            item_name=rec.get("name", ""),
            order_time=order_time,
            logistic_type=rec.get("logi", ""),
            imported_at=datetime.now(TH_TZ),
            import_date=import_date,
        )

        # ผูก Product ถ้ามี FK
        if has_product_fk:
            prod = Product.query.filter_by(sku=sku).first()
            if not prod:
                prod = Product(sku=sku, brand="", model=rec.get("name", ""))
                db.session.add(prod)
                db.session.flush()
            ol_kwargs["product_id"] = prod.id

        line = OrderLine(**ol_kwargs)
        db.session.add(line)
        added += 1

    db.session.commit()

    # แจ้งผลสรุป
    flash(f"นำเข้าออเดอร์สำเร็จ: เพิ่ม {added} อัปเดต {updated}", "success")

    # เตือนรายการที่ซ้ำ (บอกวันที่เคยนำเข้า)
    if imported_dates:
        dates_th = " / ".join(sorted(d.strftime("%d/%m/%Y") for d in imported_dates))
        flash(f"คำเตือน: พบเลข Order ที่เคยนำเข้าแล้วเมื่อวันที่ {dates_th}", "warning")

    return added, updated

