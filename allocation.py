
from collections import defaultdict
from datetime import datetime
from sqlalchemy import func, text
from utils import PLATFORM_PRIORITY, now_thai, sla_status, due_date_for, normalize_platform, TH_TZ
from models import db, Shop, Product, Stock, Sales, OrderLine

def compute_allocation(session, filters:dict):
    """
    คืน list ของ dict ครบทุกคอลัมน์ที่จอ Dashboard ต้องใช้
    
    Logic การจัดสรร (แก้ไขตาม Requirement):
    1. เรียง Priority: Shopee > TikTok > Lazada > อื่นๆ, แล้วตามเวลาสั่ง (มาก่อนได้ก่อน)
    2. Order ที่ Packed / Cancelled / เปิดใบขายครบ -> ไม่นำ Qty มาคำนวณ (ข้ามการตัดสต็อก)
    3. Order ที่ Issued (จ่ายแล้ว) / Accepted (รับแล้ว) -> ต้องนำ Qty มาตัดสต็อก (จองของไว้)
    4. Order ใหม่ -> คำนวณตัดสต็อกตามลำดับ
       - ถ้าสต็อกพอ -> READY_ACCEPT (หรือ LOW_STOCK ถ้าเหลือน้อย)
       - ถ้าสต็อกหมด -> SHORTAGE
       - ถ้าสต็อกเหลือแต่ไม่พอจำนวนที่ขอ -> NOT_ENOUGH (ไม่ตัดสต็อก)
    """
    
    # Query ข้อมูล Order ทั้งหมด
    q = session.query(OrderLine, Shop, Product, Stock, Sales)\
        .join(Shop, Shop.id==OrderLine.shop_id)\
        .outerjoin(Product, Product.sku==OrderLine.sku)\
        .outerjoin(Stock, Stock.sku==OrderLine.sku)\
        .outerjoin(Sales, Sales.order_id==OrderLine.order_id)

    # Platform / Shop ต้องกรองเสมอ
    if filters.get("platform"):
        q = q.filter(Shop.platform==filters["platform"])
    if filters.get("shop_id"):
        q = q.filter(Shop.id==filters["shop_id"])
    
    # --- [แก้ไข] แยก Logic การกรองวันที่ ---
    if filters.get("active_only") or filters.get("all_time"):
        # โหมดดูงานค้าง (active_only) หรือ All Time: ไม่กรองวันที่
        pass
    else:
        # โหมดรายงานผล: กรองตามวันที่ที่เลือก (Import Date / Order Date)
        # [แก้ไข] รองรับ import_from / import_to (Range)
        if filters.get("import_from"):
            q = q.filter(OrderLine.import_date >= filters["import_from"])
        if filters.get("import_to"):
            q = q.filter(OrderLine.import_date <= filters["import_to"])
        # กรณีเก่า (เผื่อไว้ compatibility)
        if filters.get("import_date"):
            q = q.filter(OrderLine.import_date==filters["import_date"])
        if filters.get("date_from"):
            q = q.filter(OrderLine.order_time>=filters["date_from"])
        if filters.get("date_to"):
            q = q.filter(OrderLine.order_time<filters["date_to"])
    
    # กรองตามวันที่ "กดพร้อมรับ" (Accepted At)
    if filters.get("accepted_from"):
        q = q.filter(OrderLine.accepted_at >= filters["accepted_from"])
    if filters.get("accepted_to"):
        q = q.filter(OrderLine.accepted_at < filters["accepted_to"])

    # ดึงรายการ Order ที่ยกเลิก (จากตาราง cancelled_orders)
    cancelled_order_ids = set()
    try:
        result = session.execute(text("SELECT order_id FROM cancelled_orders")).fetchall()
        cancelled_order_ids = {row[0] for row in result if row and row[0]}
    except:
        pass  # ถ้าไม่มีตาราง cancelled_orders ก็ข้ามไป

    # ดึงรายการ Order ที่จ่ายแล้ว (issued_orders)
    issued_order_ids = set()
    try:
        result = session.execute(text("SELECT order_id FROM issued_orders")).fetchall()
        issued_order_ids = {row[0] for row in result if row and row[0]}
    except:
        pass

    rows = []
    for ol, shop, prod, stock, sales in q.order_by(OrderLine.order_time.asc()).all():
        stock_qty = int(stock.qty) if stock and stock.qty is not None else 0
        brand = prod.brand if prod else ""
        model = prod.model if prod else (ol.item_name or "")
        
        # [แก้ไข] แยกแยะระหว่าง "ยังไม่นำเข้า SBS" กับ "ยังไม่มีการเปิดใบขาย"
        is_not_in_sbs = False
        if sales is None:
            # กรณีไม่มีข้อมูลในตาราง Sales เลย -> Order ยังไม่นำเข้า SBS
            s_label = "Orderยังไม่นำเข้าSBS"
            is_not_in_sbs = True
        else:
            # กรณีมีข้อมูล Sales แต่สถานะว่าง -> ยังไม่มีการเปิดใบขาย
            s_label = sales.status if sales.status else "ยังไม่มีการเปิดใบขาย"
        
        sla, due = sla_status(shop.platform, ol.order_time or now_thai())
        
        # เช็คสถานะ Packed / เปิดใบขายครบ (ระวัง: ต้องไม่นับ "Orderยังไม่นำเข้าSBS" เป็น packed)
        is_packed = False
        if s_label and not is_not_in_sbs:
            s_lower = s_label.lower()
            if any(keyword in s_lower for keyword in ["ครบตามจำนวน", "packed", "แพ็คแล้ว", "opened_full"]):
                is_packed = True
        
        is_cancelled = ol.order_id in cancelled_order_ids
        is_issued = ol.order_id in issued_order_ids
        
        # ถ้าเป็นโหมด active_only (ดูงานค้าง) ให้ข้ามพวกที่จบงานแล้วไปเลย
        if filters.get("active_only"):
            if is_packed or is_cancelled:
                continue
        
        rows.append({
            "id": ol.id,
            "platform": shop.platform,
            "shop": shop.name,
            "shop_id": shop.id,
            "order_id": ol.order_id,
            "sku": ol.sku,
            "brand": brand,
            "model": model,
            "stock_qty": stock_qty,
            "qty": int(ol.qty or 0),
            "order_time": ol.order_time,
            "order_time_iso": (ol.order_time.astimezone(TH_TZ).isoformat() if ol.order_time else ""),
            "due_date": due,
            "sla": sla,
            "logistic": ol.logistic_type or "",
            "sales_status": s_label,
            "is_not_in_sbs": is_not_in_sbs,  # [เพิ่ม] flag สำหรับ Order ยังไม่นำเข้า SBS
            "accepted": bool(ol.accepted),
            "accepted_by": ol.accepted_by_username or "",
            "dispatch_round": ol.dispatch_round if hasattr(ol, 'dispatch_round') else None,
            "printed_warehouse": ol.printed_warehouse if hasattr(ol, 'printed_warehouse') else 0,
            "printed_warehouse_at": ol.printed_warehouse_at if hasattr(ol, 'printed_warehouse_at') else None,
            "printed_warehouse_by": ol.printed_warehouse_by if hasattr(ol, 'printed_warehouse_by') else None,
            "printed_picking": ol.printed_picking if hasattr(ol, 'printed_picking') else 0,
            "printed_picking_at": ol.printed_picking_at if hasattr(ol, 'printed_picking_at') else None,
            "printed_picking_by": ol.printed_picking_by if hasattr(ol, 'printed_picking_by') else None,
            "is_packed": is_packed,
            "is_cancelled": is_cancelled,
            "is_issued": is_issued,
            "allocation_status": "",  # จะคำนวณในขั้นตอนถัดไป
        })

    # คำนวณ AllQty (ยอดรวมที่ต้องใช้ต่อ SKU) - นับเฉพาะที่ยังไม่ Packed/Cancelled
    sku_total = defaultdict(int)
    for r in rows:
        if not r["is_packed"] and not r["is_cancelled"]:
            sku_total[r["sku"]] += r["qty"]
    
    for r in rows:
        r["allqty"] = sku_total[r["sku"]]

    # จัดสรรสต็อกตาม Priority
    by_sku = defaultdict(list)
    for r in rows:
        by_sku[r["sku"]].append(r)
    
    for sku, arr in by_sku.items():
        # เรียงตาม Priority: Platform > Order Time
        arr.sort(key=lambda x: (
            PLATFORM_PRIORITY.get(x["platform"], 999), 
            x["order_time"] or datetime.max
        ))
        
        # สต็อกเริ่มต้นของ SKU นี้
        current_stock = arr[0]["stock_qty"]
        
        for r in arr:
            # ข้อ 3: Order ที่ Packed หรือ Cancelled -> ไม่ดึง Qty มาคำนวณ (จบงานแล้ว)
            if r["is_packed"]:
                r["allocation_status"] = "PACKED"
                continue
            
            if r["is_cancelled"]:
                r["allocation_status"] = "CANCELLED"
                continue
            
            # --- คำนวณสถานะก่อน (ยังไม่ดู Issued/Accepted) ---
            req_qty = r["qty"]
            calculated_status = ""
            
            if current_stock <= 0:
                calculated_status = "SHORTAGE"
            elif current_stock < req_qty:
                calculated_status = "NOT_ENOUGH"
            else:
                # สต็อกพอ -> ถ้าเหลือน้อยเป็น LOW_STOCK
                if current_stock - req_qty <= 3:
                    calculated_status = "LOW_STOCK"
                else:
                    calculated_status = "READY_ACCEPT"
            
            # --- บันทึกสถานะ และ ตัดสต็อก ---
            
            # [แก้ไข] ย้ายเช็ค Accepted ขึ้นมาก่อน Issued
            # เพื่อให้ถ้ากดรับแล้ว สถานะต้องเป็น "ACCEPTED" (รับแล้ว) เท่านั้น
            if r["accepted"]:
                r["allocation_status"] = "ACCEPTED"
                # ตัดสต็อกเฉพาะกรณีของพอ
                if calculated_status in ["READY_ACCEPT", "LOW_STOCK"]:
                    current_stock -= req_qty
                continue
            
            # ถ้า Issued (จ่ายแล้ว) -> คงสถานะที่คำนวณได้ไว้ (เช่น LOW_STOCK, SHORTAGE)
            # และทำการตัดสต็อก (ถ้าของพอ) เพื่อจองของ
            if r["is_issued"]:
                r["allocation_status"] = calculated_status  # เก็บสถานะจริงไว้
                # ตัดสต็อกเฉพาะกรณีของพอ (READY_ACCEPT หรือ LOW_STOCK)
                if calculated_status in ["READY_ACCEPT", "LOW_STOCK"]:
                    current_stock -= req_qty
                continue
            
            # --- Order ใหม่ / ยังไม่ดำเนินการ ---
            if calculated_status in ["SHORTAGE", "NOT_ENOUGH"]:
                r["allocation_status"] = calculated_status
                # ของไม่พอ ไม่ตัดสต็อก
            else:
                r["allocation_status"] = calculated_status
                current_stock -= req_qty  # ของพอ จองของไว้

    # --- KPI (ปรับปรุงใหม่) ---
    # Helper: รายการที่ยัง Active (ยังไม่ Pack/Cancel)
    all_active_rows = [r for r in rows if not r["is_packed"] and not r["is_cancelled"]]
    unique_active_orders = set(r["order_id"] for r in all_active_rows)
    
    # Order Ready ที่ "ทำงานได้จริง" (ต้องยังไม่ Accepted และยังไม่ Issued)
    orders_ready_actionable = set(r["order_id"] for r in all_active_rows 
                                  if r["allocation_status"] == "READY_ACCEPT" 
                                  and not r["accepted"] 
                                  and not r["is_issued"])
    
    # Order Low Stock ที่ "ทำงานได้จริง" (ต้องยังไม่ Accepted และยังไม่ Issued)
    orders_low_actionable = set(r["order_id"] for r in all_active_rows 
                                if r["allocation_status"] == "LOW_STOCK" 
                                and not r["accepted"] 
                                and not r["is_issued"])

    kpis = {
        "total_items": len(rows),
        "total_qty": sum(r["qty"] for r in rows),
        # [เพิ่ม] รวม Order ทั้งหมด (ตามล็อตที่เลือก - ใช้เมื่อกรองวันที่)
        "orders_total": len(set(r["order_id"] for r in rows if r["order_id"])),
        # [แก้ไข] รวม Orderค้าง: นับเฉพาะ Active (ยังไม่ Pack/Cancel) รวม Issued ด้วย
        "orders_unique": len(unique_active_orders),
        "ready": sum(1 for r in rows if r["allocation_status"]=="READY_ACCEPT"),
        "accepted": sum(1 for r in rows if r["allocation_status"]=="ACCEPTED"),
        "low": sum(1 for r in rows if r["allocation_status"]=="LOW_STOCK"),
        "nostock": sum(1 for r in rows if r["allocation_status"]=="SHORTAGE"),
        "notenough": sum(1 for r in rows if r["allocation_status"]=="NOT_ENOUGH"),
        "packed": sum(1 for r in rows if r["allocation_status"]=="PACKED"),
        # [แก้ไข] ปุ่มกดจ่ายงาน (Actionable): ไม่นับ Issued เพื่อป้องกันจ่ายซ้ำ
        "orders_ready": len(orders_ready_actionable),
        "orders_low": len(orders_low_actionable),
        "orders_cancelled": len(set(r["order_id"] for r in rows if r["is_cancelled"])),
        # นับ Order ที่ยังไม่นำเข้า SBS (ใช้ flag is_not_in_sbs)
        "orders_not_in_sbs": len(set(r["order_id"] for r in rows if r.get("is_not_in_sbs"))),
        # นับเฉพาะที่มี Sales Record แต่ยังไม่เปิดใบขาย (ไม่รวม Not In SBS)
        "orders_nosales": len(set(r["order_id"] for r in rows if not r.get("is_not_in_sbs") and r.get("sales_status") == "ยังไม่มีการเปิดใบขาย")),
    }
    
    return rows, kpis
