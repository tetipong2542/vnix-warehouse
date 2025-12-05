# services/lowstock_queue.py
"""
ดึงข้อมูลสินค้าน้อยจากคิวออเดอร์ (allocation_status = 'LOW_STOCK')
ใช้ร่วมกันระหว่าง Dashboard badge และรายงานสินค้าน้อย
"""

from sqlalchemy import func


def get_lowstock_rows_from_allocation(rows):
    """
    ดึง SKU ที่มี allocation_status = 'LOW_STOCK' จาก compute_allocation
    และรวมข้อมูลตาม SKU
    
    Args:
        rows: ผลลัพธ์จาก compute_allocation (list of dict)
    
    Returns:
        list of dict: รายการ SKU ที่อยู่ในคิวสินค้าน้อย
    """
    # กรองเฉพาะที่มีสถานะ LOW_STOCK
    lowstock_rows = [
        r for r in rows 
        if r.get("allocation_status") == "LOW_STOCK"
    ]
    
    # รวมข้อมูลตาม SKU
    sku_map = {}
    for r in lowstock_rows:
        sku = r.get("sku", "").strip()
        if not sku:
            continue
        
        if sku not in sku_map:
            sku_map[sku] = {
                "sku": sku,
                "brand": r.get("brand", ""),
                "model": r.get("model", ""),
                "location": "",  # จะเติมจาก Product ถ้ามี
                "min_stock": 3,  # threshold ที่ใช้จัดคิว LOW_STOCK
                "onhand": r.get("stock_qty", 0),
                "reserved": 0,
                "required_qty": 0,  # จำนวนที่ออเดอร์ต้องการ
                "orders_count": 0,  # จำนวนออเดอร์
                "order_ids": set(),  # เก็บ order_id ไว้นับ
            }
        
        # สะสมข้อมูล
        sku_data = sku_map[sku]
        sku_data["required_qty"] += r.get("qty", 0)
        
        order_id = r.get("order_id", "").strip()
        if order_id:
            sku_data["order_ids"].add(order_id)
    
    # แปลงเป็น list และคำนวณค่าเพิ่มเติม
    result = []
    for sku, data in sku_map.items():
        onhand = data["onhand"]
        reserved = data["reserved"]
        required_qty = data["required_qty"]
        
        available = onhand - reserved
        shortage = max(0, required_qty - available)
        remain_after_pick = available - required_qty
        
        data["available"] = available
        data["shortage"] = shortage
        data["remain_after_pick"] = remain_after_pick
        data["orders_count"] = len(data["order_ids"])
        
        # ลบ order_ids ออกก่อน return
        del data["order_ids"]
        
        result.append(data)
    
    # เรียงตาม shortage มากสุดก่อน
    result.sort(key=lambda x: (-x["shortage"], x["sku"]))
    
    return result


def count_lowstock_skus(rows):
    """
    นับจำนวน SKU ที่อยู่ในคิวสินค้าน้อย
    
    Args:
        rows: ผลลัพธ์จาก compute_allocation
    
    Returns:
        int: จำนวน SKU ที่มีสถานะ LOW_STOCK
    """
    lowstock_skus = {
        r.get("sku") 
        for r in rows 
        if r.get("allocation_status") == "LOW_STOCK" and r.get("sku")
    }
    return len(lowstock_skus)


def count_lowstock_orders(rows):
    """
    นับจำนวนออเดอร์ที่อยู่ในคิวสินค้าน้อย (ออเดอร์ที่มีรายการสินค้าน้อย)
    
    Args:
        rows: ผลลัพธ์จาก compute_allocation
    
    Returns:
        int: จำนวนออเดอร์
    """
    lowstock_orders = {
        r.get("order_id")
        for r in rows
        if r.get("allocation_status") == "LOW_STOCK" and r.get("order_id")
    }
    return len(lowstock_orders)


def compute_totals(rows):
    """
    คำนวณสรุปยอดรวมจากรายการสินค้าน้อย
    
    Args:
        rows: รายการจาก get_lowstock_rows_from_allocation
    
    Returns:
        dict: ยอดรวมต่างๆ
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


def filter_lowstock_rows(rows, keyword=None):
    """
    กรองรายการสินค้าน้อยตามเงื่อนไข
    
    Args:
        rows: รายการจาก get_lowstock_rows_from_allocation
        keyword: คำค้นหา SKU/Brand/Model
    
    Returns:
        list: รายการที่ผ่านการกรอง
    """
    result = rows
    
    # กรองตาม keyword
    if keyword:
        kw = keyword.lower()
        result = [
            r for r in result
            if kw in r["sku"].lower()
            or kw in r["brand"].lower()
            or kw in r["model"].lower()
        ]
    
    return result
