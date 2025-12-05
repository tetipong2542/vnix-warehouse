
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

# เพิ่มคีย์หัวคอลัมน์สำหรับ "ชื่อร้าน"
COMMON_SHOP = ["ชื่อร้าน","Shop","Shop Name","Store","Store Name","ร้าน","ร้านค้า"]

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

def clean_shop_name(s) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    # ตัด "(Shopee)" หรือ "(Lazada)" ที่มาจาก datalist
    if s.endswith(")") and " (" in s:
        try:
            s = s[:s.rfind(" (")].strip()
        except Exception:
            pass
    # ตัดสัญลักษณ์พิเศษ เช่น "•"
    s = s.replace("•", " ").strip()
    return s

def get_or_create_shop(platform, shop_name):
    platform = normalize_platform(platform)
    name = clean_shop_name(shop_name)
    shop = Shop.query.filter_by(name=name).first()
    if not shop:
        shop = Shop(platform=platform, name=name)
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
def import_orders(df: pd.DataFrame, platform: str, shop_name: str | None, import_date: date) -> tuple[int, int]:
    """
    เพิ่มเฉพาะรายการที่ 'ยังไม่เคยมี' ในระบบ
    - รองรับอ่าน 'ชื่อร้าน' จากไฟล์ (คอลัมน์: COMMON_SHOP) ถ้าไม่มีให้ใช้ค่าที่ผู้ใช้กรอก
    - key กันซ้ำ = (shop + order_id + sku)
    """
    platform_std = normalize_platform(platform)

    # --- หา columns จากหลายแพลตฟอร์ม ---
    shop_col  = first_existing(df, COMMON_SHOP)
    order_col = first_existing(df, COMMON_ORDER_ID)
    sku_col   = first_existing(df, COMMON_SKU)
    name_col  = first_existing(df, COMMON_ITEM_NAME)
    qty_col   = first_existing(df, COMMON_QTY)
    time_col  = first_existing(df, COMMON_ORDER_TIME)
    logi_col  = first_existing(df, COMMON_LOGISTICS)

    if not order_col or not sku_col:
        raise ValueError("ไม่พบคอลัมน์ Order ID หรือ SKU ในไฟล์")

    # fallback ชื่อร้านจากฟอร์ม (ถ้ามี)
    fallback_shop = clean_shop_name(shop_name) if shop_name else ""

    # รวมแถวซ้ำภายในไฟล์: key = (shop + order_id + sku)
    grouped: dict[tuple[str, str, str], dict] = {}
    for _, row in df.iterrows():
        oid = str(row.get(order_col, "")).strip()
        sku = str(row.get(sku_col, "")).strip()
        if not oid or not sku:
            continue

        sname = clean_shop_name(row.get(shop_col)) if shop_col else fallback_shop
        if not sname:
            sname = ""  # ข้ามทีหลัง

        qty = pd.to_numeric(row.get(qty_col), errors="coerce") if qty_col else None
        qty = int(qty) if pd.notnull(qty) else 1

        key = (sname, oid, sku)
        rec = grouped.get(key, {
            "shop": sname,
            "qty": 0,
            "name": str(row.get(name_col, "") or ""),
            "time": row.get(time_col) if time_col else None,
            "logi": str(row.get(logi_col, "") or "") if logi_col else "",
        })
        rec["qty"] += max(qty, 0)
        if not rec.get("name"):
            rec["name"] = str(row.get(name_col, "") or "")
        rec["time"] = row.get(time_col) if time_col else rec.get("time")
        rec["logi"] = str(row.get(logi_col, "") or "") if logi_col else rec.get("logi")
        grouped[key] = rec

    if not grouped:
        raise ValueError("ไฟล์ว่างหรือไม่มีแถวข้อมูลออเดอร์ที่ถูกต้อง")

    imported_dates: set[date] = set()
    missing_shop_count = 0
    added, updated = 0, 0

    has_product_fk = hasattr(OrderLine, "product_id")

    for (sname, oid, sku), rec in grouped.items():
        if not sname:
            missing_shop_count += 1
            continue

        shop = get_or_create_shop(platform_std, sname)

        # กันซ้ำระดับแอป: (shop + order_id + sku)
        exists = OrderLine.query.filter_by(shop_id=shop.id, order_id=oid, sku=sku).first()
        if exists:
            if hasattr(exists, "import_date") and isinstance(exists.import_date, date):
                imported_dates.add(exists.import_date)
            continue

        order_time = parse_datetime_guess(rec.get("time")) if rec.get("time") is not None else None

        ol_kwargs = dict(
            platform=platform_std,
            shop_id=shop.id,
            order_id=oid,
            sku=sku,
            item_name=rec.get("name", "")[:255],
            qty=int(rec.get("qty") or 0) or 1,
            order_time=order_time,
            logistic_type=(rec.get("logi") or "")[:60],
            import_date=import_date,
        )

        # ผูก product ถ้าตารางมีและเจอสินค้า
        if has_product_fk:
            prod = Product.query.filter_by(sku=sku).first()
            if prod:
                ol_kwargs["product_id"] = prod.id

        line = OrderLine(**ol_kwargs)
        db.session.add(line)
        added += 1

    db.session.commit()

    if missing_shop_count > 0 and not shop_col and not fallback_shop:
        flash(f"ข้าม {missing_shop_count} แถว เพราะไม่ได้ระบุ 'ชื่อร้าน' ทั้งในไฟล์และฟอร์ม", "warning")
    flash(f"นำเข้าออเดอร์สำเร็จ: เพิ่ม {added} อัปเดต {updated}", "success")

    return added, updated