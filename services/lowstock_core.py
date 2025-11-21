# services/lowstock_core.py
"""
โมดูลกลางสำหรับคำนวณสินค้าน้อย (Low Stock)
ใช้ร่วมกันระหว่าง Dashboard และรายงานสินค้าน้อย เพื่อให้ตัวเลขตรงกัน
"""

from typing import List, Dict, Optional
from sqlalchemy import func
from datetime import datetime


def _safe_int(value, default=0):
    """แปลงค่าเป็น int อย่างปลอดภัย"""
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def lowstock_rows_from_allocation(rows: List[dict], 
                                   keyword: Optional[str] = None,
                                   platform: Optional[str] = None,
                                   shop_id: Optional[int] = None) -> List[Dict]:
    """
    คำนวณสินค้าน้อยจากผลลัพธ์ของ compute_allocation
    
    Args:
        rows: ผลลัพธ์จาก compute_allocation
        keyword: คำค้นหา SKU/Brand/Model
        platform: กรองตามแพลตฟอร์ม
        shop_id: กรองตาม shop_id
    
    Returns:
        List ของ SKU ที่มีสินค้าน้อย พร้อมข้อมูลการคำนวณ
    """
    
    # กรองเฉพาะแถวที่ยังไม่ได้กดรับและไม่ใช่ PACKED
    active_rows = [
        r for r in rows
        if not r.get("accepted", False)
        and (r.get("allocation_status", "") or "").upper() != "PACKED"
    ]
    
    # กรองตาม keyword
    if keyword:
        kw = keyword.lower()
        active_rows = [
            r for r in active_rows
            if kw in (r.get("sku") or "").lower()
            or kw in (r.get("brand") or "").lower()
            or kw in (r.get("model") or "").lower()
        ]
    
    # กรองตาม platform
    if platform:
        active_rows = [r for r in active_rows if r.get("platform") == platform]
    
    # กรองตาม shop_id
    if shop_id:
        active_rows = [r for r in active_rows if r.get("shop_id") == shop_id]
    
    # รวมข้อมูลตาม SKU
    sku_map = {}
    for r in active_rows:
        sku = (r.get("sku") or "").strip()
        if not sku:
            continue
        
        if sku not in sku_map:
            sku_map[sku] = {
                "sku": sku,
                "brand": r.get("brand", ""),
                "model": r.get("model", ""),
                "location": r.get("location", "") or r.get("bin_code", ""),
                "min_stock": _safe_int(r.get("min_stock", 0)),
                "stock_qty": _safe_int(r.get("stock_qty", 0)),
                "onhand": _safe_int(r.get("stock_qty", 0)),
                "reserved": 0,  # จะคำนวณจากออเดอร์
                "required_qty": 0,  # รวมจำนวนที่ออเดอร์ต้องการ
                "orders_count": 0,  # จำนวนออเดอร์ที่ต้องการ SKU นี้
            }
        
        # รวมจำนวนที่ต้องการจากออเดอร์
        qty = _safe_int(r.get("qty", 0))
        sku_map[sku]["required_qty"] += qty
        sku_map[sku]["orders_count"] += 1
    
    # คำนวณ available, shortage, remain_after_pick
    result = []
    for sku, data in sku_map.items():
        onhand = data["onhand"]
        reserved = data["reserved"]
        required_qty = data["required_qty"]
        min_stock = data["min_stock"]
        
        available = onhand - reserved
        shortage = max(0, required_qty - available)
        remain_after_pick = available - required_qty
        
        # เงื่อนไขสินค้าน้อย: ขาดจากที่ต้องการ หรือ น้อยกว่า min_stock
        is_low = (shortage > 0) or (available < min_stock and min_stock > 0)
        
        if is_low:
            data["available"] = available
            data["shortage"] = shortage
            data["remain_after_pick"] = remain_after_pick
            result.append(data)
    
    # เรียงตาม shortage มากสุดก่อน
    result.sort(key=lambda x: (-x["shortage"], x["sku"]))
    
    return result


def lowstock_count_from_allocation(rows: List[dict],
                                    keyword: Optional[str] = None,
                                    platform: Optional[str] = None,
                                    shop_id: Optional[int] = None) -> int:
    """
    นับจำนวน SKU ที่มีสินค้าน้อย
    
    Args:
        rows: ผลลัพธ์จาก compute_allocation
        keyword: คำค้นหา SKU/Brand/Model
        platform: กรองตามแพลตฟอร์ม
        shop_id: กรองตาม shop_id
    
    Returns:
        จำนวน SKU ที่มีสินค้าน้อย
    """
    return len(lowstock_rows_from_allocation(rows, keyword, platform, shop_id))


def lowstock_orders_count(rows: List[dict]) -> int:
    """
    นับจำนวนออเดอร์ที่มีสินค้าน้อย (เงื่อนไขเดิมจาก _orders_lowstock_order_set)
    
    Args:
        rows: ผลลัพธ์จาก compute_allocation
    
    Returns:
        จำนวนออเดอร์ที่มีสินค้าน้อย
    """
    by_oid = {}
    for r in rows:
        oid = (r.get("order_id") or "").strip()
        if not oid:
            continue
        by_oid.setdefault(oid, []).append(r)
    
    result_set = set()
    for oid, items in by_oid.items():
        if not items:
            continue
        all_sendable = True
        has_low = False
        for it in items:
            status = (it.get("allocation_status") or "").upper()
            accepted = bool(it.get("accepted", False))
            packed = (status == "PACKED") or bool(it.get("packed", False))
            if packed or accepted:
                all_sendable = False
                break
            if status not in ("READY_ACCEPT", "LOW_STOCK"):
                all_sendable = False
                break
            if status == "LOW_STOCK":
                has_low = True
        if all_sendable and has_low:
            result_set.add(oid)
    
    return len(result_set)


def compute_totals(rows: List[Dict]) -> Dict:
    """
    คำนวณสรุปยอดรวมจากรายการสินค้าน้อย
    
    Args:
        rows: รายการสินค้าน้อยจาก lowstock_rows_from_allocation
    
    Returns:
        dict ของยอดรวม
    """
    return {
        "total_skus": len(rows),
        "sum_onhand": sum(r["onhand"] for r in rows),
        "sum_reserved": sum(r["reserved"] for r in rows),
        "sum_available": sum(r["available"] for r in rows),
        "sum_required": sum(r["required_qty"] for r in rows),
        "sum_shortage": sum(r["shortage"] for r in rows),
        "sum_orders": sum(r["orders_count"] for r in rows),
    }
