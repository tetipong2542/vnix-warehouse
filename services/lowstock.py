
# -*- coding: utf-8 -*-
"""
รายงานสินค้าน้อย (Low Stock) — V4.0 Patch
--------------------------------------------

สิ่งที่แก้/เพิ่มในไฟล์นี้
1) ผูกข้อมูลรายงานกับ "ฟังก์ชันสินค้าน้อย" โดยตรง (นับจำนวน SKU จากแหล่งสินค้าน้อยที่แท้จริง)
2) คัดกรองเฉพาะ Order Line ที่ SKU อยู่ในชุดสินค้าน้อย (แก้ปัญหาจำนวน SKU รายงานเพี้ยน)
3) ปรับคอลัมน์ที่ใช้ในรายงานตามที่ขอ และลบคอลัมน์ที่ไม่ใช้แล้ว
4) จัดกลุ่มแถวตามเลขออเดอร์ + ตีกรอบหนาบน/ล่างและคงให้อยู่ติดกันเสมอ
5) ใส่ hook สำหรับนับจำนวนครั้งที่พิมพ์ และเวลาที่พิมพ์
6) รองรับการ Export/Template ได้ง่าย (แปลงเป็น list[dict])

NOTE: ตัวอย่างนี้ใช้สไตล์ Flask + Jinja2 + pandas.
หากโปรเจกต์ใช้โครงอื่น ให้ปรับ import/router ให้เข้ากัน (FastAPI/Starlette ก็ใช้หลักการเดียวกัน)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

try:
    # ถ้ามี Flask จริงในโปรเจกต์จะใช้ได้เลย
    from flask import Blueprint, render_template, request, current_app
except Exception:
    # เพื่อให้ไฟล์ import ได้แม้ไม่ได้ใช้ Flask ตรง ๆ (เช่นรันทดสอบ)
    Blueprint = None
    render_template = None
    request = None
    current_app = None

# ---- คุณอาจมี pandas อยู่ใน venv ตาม V4.0 อยู่แล้ว ----
import pandas as pd

# -----------------------------
# Utilities
# -----------------------------

DISPLAY_COLS = [
    "platform", "store", "order_no", "sku", "brand", "product_name",
    "stock", "qty", "allqty", "order_time", "due_date", "sla",
    "shipping_type", "assign_round", "printed_count", "printed_at"
]

THAI_HEADER = [
    "แพลตฟอร์ม", "ร้าน", "เลข Order", "SKU", "Brand", "ชื่อสินค้า",
    "Stock", "Qty", "AllQty", "เวลาที่ลูกค้าสั่ง", "กำหนดส่ง", "SLA",
    "ประเภทขนส่ง", "จ่ายงาน(รอบที่)", "พิมพ์แล้ว(ครั้ง)", "วัน/เดือน/ปี/เวลา ที่พิมพ์"
]

COL_SYNONYMS = {
    "platform": ["platform", "แพลตฟอร์ม", "channel", "ch"],
    "store": ["store", "shop", "ร้าน", "seller_name"],
    "order_no": ["order_no", "order", "เลขสั่งซื้อ", "so_no", "order_id"],
    "sku": ["sku", "SKU", "รหัสสินค้า"],
    "brand": ["brand", "ยี่ห้อ"],
    "product_name": ["product_name", "name", "ชื่อสินค้า", "item_name", "title"],
    "stock": ["stock", "on_hand", "available_stock", "คงเหลือ"],
    "qty": ["qty", "quantity", "จำนวน"],
    "order_time": ["order_time", "ordered_at", "เวลาที่ลูกค้าสั่ง", "create_time"],
    "due_date": ["due_date", "ship_by", "กำหนดส่ง", "promise_date", "sla_due"],
    "sla": ["sla", "SLA"],
    "shipping_type": ["shipping_type", "logistics", "ประเภทขนส่ง", "shipment_type"],
    "assign_round": ["assign_round", "จ่ายงาน", "assign_no", "round_no"],
    "printed_count": ["printed_count", "print_count", "พิมพ์แล้ว"],
    "printed_at": ["printed_at", "print_time", "printed_time", "วันเวลาที่พิมพ์"],
}

REMOVE_COLS = ["min_stock", "reserved", "available", "shortage", "remaining_after_pick", "orders_count"]

def _find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand in df.columns:
            return cand
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    return None

def _rename_to_canonical(df: pd.DataFrame) -> pd.DataFrame:
    """พยายาม map ชื่อคอลัมน์ของ df ให้เป็นชื่อมาตรฐานตาม DISPLAY_COLS"""
    mapping = {}
    for canon, syns in COL_SYNONYMS.items():
        col = _find_col(df, syns)
        if col:
            mapping[col] = canon
    if mapping:
        df = df.rename(columns=mapping)
    # เติมคอลัมน์ที่จำเป็น ถ้าไม่มี
    for need in DISPLAY_COLS:
        if need not in df.columns:
            df[need] = None
    return df

# -----------------------------
# Data Sources (ต้องผูกกับฟังก์ชันเดิม)
# -----------------------------

from sqlalchemy import text
from allocation import compute_allocation
from models import db
from services.lowstock_queue import get_lowstock_rows_from_allocation

def get_low_stock_df_adapter() -> pd.DataFrame:
    """
    ดึง "สินค้าน้อย" จากฟังก์ชันเดิมของโปรเจกต์ (compute_allocation -> get_lowstock_rows_from_allocation)
    """
    try:
        # 1. ดึงข้อมูลทั้งหมดจาก compute_allocation
        # filters={} หมายถึงดึงทั้งหมด (หรืออาจจะใส่ filter ตาม request ก็ได้ แต่ที่นี่เอาทั้งหมดก่อน)
        rows, _ = compute_allocation(db.session, {})
        
        # 2. กรอง Cancelled / Issued (เลียนแบบ app.py)
        try:
            cancelled_oids = {r[0] for r in db.session.execute(text("SELECT order_id FROM cancelled_orders")).fetchall()}
            rows = [r for r in rows if r.get("order_id") not in cancelled_oids]
        except Exception:
            pass 

        try:
            issued_oids = {r[0] for r in db.session.execute(text("SELECT order_id FROM issued_orders")).fetchall()}
            rows = [r for r in rows if r.get("order_id") not in issued_oids]
        except Exception:
            pass

        # 3. ดึงเฉพาะ Low Stock (ใช้ logic เดิมของโปรเจกต์)
        items = get_lowstock_rows_from_allocation(rows)
        
        # 4. แปลงเป็น DataFrame
        if not items:
            return pd.DataFrame(columns=["sku", "stock"])
            
        df = pd.DataFrame(items)
        
        # 5. Rename columns to match canonical
        # items มี key: sku, onhand, ...
        df = df.rename(columns={"onhand": "stock"})
        
        return _rename_to_canonical(df)
        
    except Exception as e:
        raise RuntimeError(f"Error fetching low stock data: {e}") from e


def get_open_order_lines_df_adapter() -> pd.DataFrame:
    """
    ดึงรายการ Order เฉพาะสถานะที่ยังต้องจัด (Open/Waiting pick ฯลฯ)
    """
    try:
        # 1. ดึงข้อมูลทั้งหมด
        rows, _ = compute_allocation(db.session, {})
        
        # 2. กรอง Cancelled / Issued
        try:
            cancelled_oids = {r[0] for r in db.session.execute(text("SELECT order_id FROM cancelled_orders")).fetchall()}
            rows = [r for r in rows if r.get("order_id") not in cancelled_oids]
        except Exception:
            pass

        try:
            issued_oids = {r[0] for r in db.session.execute(text("SELECT order_id FROM issued_orders")).fetchall()}
            rows = [r for r in rows if r.get("order_id") not in issued_oids]
        except Exception:
            pass
            
        # 3. กรองเฉพาะ Open Orders (ตัด PACKED ออก)
        active_rows = [r for r in rows if r.get("allocation_status") != "PACKED"]
        
        if not active_rows:
             return pd.DataFrame(columns=["platform", "store", "order_no", "sku", "product_name", "qty"])

        df = pd.DataFrame(active_rows)
        
        # 4. Rename columns to match canonical
        # compute_allocation returns: platform, shop, order_id, sku, brand, model, qty, order_time, due_date, sla, logistic
        rename_map = {
            "shop": "store",
            "order_id": "order_no",
            "model": "product_name",
            "logistic": "shipping_type"
        }
        df = df.rename(columns=rename_map)
        
        return _rename_to_canonical(df)

    except Exception as e:
        raise RuntimeError(f"Error fetching open orders: {e}") from e

# -----------------------------
# Composer
# -----------------------------

def compose_lowstock_report(df_orders: pd.DataFrame, df_low: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """รวมข้อมูล order กับสินค้าน้อย -> เตรียมใช้ในรายงาน"""
    df_orders = _rename_to_canonical(df_orders.copy())
    df_low = _rename_to_canonical(df_low.copy())

    # ลบคอลัมน์ที่ไม่ใช้ถ้ามีใน source
    for col in REMOVE_COLS:
        if col in df_orders.columns:
            df_orders = df_orders.drop(columns=[col], errors="ignore")
        if col in df_low.columns:
            df_low = df_low.drop(columns=[col], errors="ignore")

    # จำกัดเฉพาะ SKU ที่เป็นสินค้าน้อย (แก้ปัญหา 11 vs 17)
    low_skus = set(df_low["sku"].astype(str).str.strip())
    df = df_orders[df_orders["sku"].astype(str).str.strip().isin(low_skus)].copy()

    # Join เพื่อเติม stock จาก low stock
    df = df.merge(df_low[["sku", "stock"]].drop_duplicates("sku"), on="sku", how="left", suffixes=("", "_ls"))

    # AllQty = ยอดที่ต้องหยิบรวมของ SKU นั้นในรายงานทั้งหมด
    if "qty" in df.columns:
        df["allqty"] = df.groupby("sku")["qty"].transform("sum")
    else:
        df["allqty"] = None

    # SLA: ถ้ายังไม่มีให้คำนวณจาก due_date - order_time (เป็นชั่วโมง)
    if "sla" not in df.columns or df["sla"].isnull().all():
        def _calc_sla(row):
            try:
                od = pd.to_datetime(row.get("order_time"))
                dd = pd.to_datetime(row.get("due_date"))
                if pd.isna(od) or pd.isna(dd):
                    return None
                delta = dd - od
                return round(delta.total_seconds() / 3600.0, 2)
            except Exception:
                return None
        df["sla"] = df.apply(_calc_sla, axis=1)

    # ค่าเริ่มต้นของคอลัมน์เกี่ยวกับการพิมพ์
    if "assign_round" not in df.columns:
        df["assign_round"] = 1
    if "printed_count" not in df.columns:
        df["printed_count"] = 0
    # printed_at จะเติมใน route

    # จัดเรียงให้ Order เดียวกันอยู่ติดกันเสมอ
    sort_cols = [c for c in ["order_no", "order_time", "platform", "store", "sku"] if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, kind="mergesort")  # mergesort เพื่อรักษากลุ่มให้ stable

    # สรุปจำนวน SKU รายงาน = จำนวน SKU ไม่ซ้ำจาก df_low (ตรงกับ Dashboard)
    # ใช้ df_low["sku"].nunique() แทน len(low_skus) เพราะ low_skus อาจมีรายการซ้ำ
    unique_low_skus = df_low["sku"].dropna().astype(str).str.strip().unique()
    summary = {
        "sku_count": len(unique_low_skus),
        "low_skus": sorted(unique_low_skus)
    }

    # คุมคอลัมน์ผลลัพธ์ให้ครบตามที่ต้องการ
    for need in DISPLAY_COLS:
        if need not in df.columns:
            df[need] = None
    df_out = df[DISPLAY_COLS].copy()

    return df_out, summary

# -----------------------------
# Router (Flask-style)
# -----------------------------

bp = Blueprint("lowstock_report", __name__, url_prefix="/reports") if Blueprint else None

@bp.route("/lowstock", methods=["GET"])
def report_page():
    """หน้า รายงานสินค้าน้อย (Low Stock) — พร้อมปุ่มพิมพ์"""
    df_low = get_low_stock_df_adapter()
    df_orders = get_open_order_lines_df_adapter()

    df_report, summary = compose_lowstock_report(df_orders, df_low)
    printed_at = datetime.now()

    # เติม printed_at ลงข้อมูลทุกรายการเพื่อแสดงในคอลัมน์
    df_report = df_report.copy()
    df_report["printed_at"] = printed_at

    rows = df_report.to_dict(orient="records")

    # ถ้าต้องนับจำนวนครั้งที่พิมพ์จริง ๆ ให้ไปเพิ่มในฟังก์ชัน log_print_event() ด้านล่าง
    # log_print_event(summary, rows)

    return render_template("report_lowstock.html",
                           rows=rows,
                           summary=summary,
                           printed_at=printed_at)


# -----------------------------
# Optional: Hook สำหรับบันทึกจำนวนครั้งที่พิมพ์
# -----------------------------

def log_print_event(summary: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    """
    ใส่โค้ดบันทึกการพิมพ์ที่นี่ (DB/ไฟล์ log/BigQuery ฯลฯ)
    ตัวอย่าง pseudo-code (ปรับตาม ORM/DB ของคุณ):

    session.add(PrintLog(
        report="lowstock",
        sku_count=summary["sku_count"],
        low_skus=",".join(summary["low_skus"]),
        printed_at=datetime.utcnow(),
        rows=len(rows),
        # user=request.user.id if มีระบบ login
    ))
    session.commit()
    """
    pass
