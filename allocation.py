
from collections import defaultdict
from datetime import datetime
from sqlalchemy import func
from utils import PLATFORM_PRIORITY, now_thai, sla_status, due_date_for, normalize_platform, TH_TZ
from models import db, Shop, Product, Stock, Sales, OrderLine

def compute_allocation(session, filters:dict):
    """คืน list ของ dict ครบทุกคอลัมน์ที่จอ Dashboard ต้องใช้"""
    q = session.query(OrderLine, Shop, Product, Stock, Sales)\
        .join(Shop, Shop.id==OrderLine.shop_id)\
        .outerjoin(Product, Product.sku==OrderLine.sku)\
        .outerjoin(Stock, Stock.sku==OrderLine.sku)\
        .outerjoin(Sales, Sales.order_id==OrderLine.order_id)

    if filters.get("platform"):
        q = q.filter(Shop.platform==filters["platform"])
    if filters.get("shop_id"):
        q = q.filter(Shop.id==filters["shop_id"])
    if filters.get("import_date"):
        q = q.filter(OrderLine.import_date==filters["import_date"])
    if filters.get("date_from"):
        q = q.filter(OrderLine.order_time>=filters["date_from"])
    if filters.get("date_to"):
        q = q.filter(OrderLine.order_time<filters["date_to"])

    rows = []
    for ol, shop, prod, stock, sales in q.order_by(OrderLine.order_time.asc()).all():
        stock_qty = int(stock.qty) if stock and stock.qty is not None else 0
        brand = prod.brand if prod else ""
        model = prod.model if prod else (ol.item_name or "")
        s_label = sales.status if sales else ""
        sla, due = sla_status(shop.platform, ol.order_time or now_thai())
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
            "qty": ol.qty,
            "order_time": ol.order_time,
            "order_time_iso": (ol.order_time.astimezone(TH_TZ).isoformat() if ol.order_time else ""),
            "due_date": due,
            "sla": sla,
            "logistic": ol.logistic_type or "",
            "sales_status": s_label or ("ยังไม่มีการเปิดใบขาย" if not sales else s_label),
            "accepted": bool(ol.accepted),
            "accepted_by": ol.accepted_by_username or "",
            "dispatch_round": ol.dispatch_round if hasattr(ol, 'dispatch_round') else None,
            "printed_warehouse": ol.printed_warehouse if hasattr(ol, 'printed_warehouse') else 0,
            "printed_warehouse_at": ol.printed_warehouse_at if hasattr(ol, 'printed_warehouse_at') else None,
            "printed_warehouse_by": ol.printed_warehouse_by if hasattr(ol, 'printed_warehouse_by') else None,
            "printed_picking": ol.printed_picking if hasattr(ol, 'printed_picking') else 0,
            "printed_picking_at": ol.printed_picking_at if hasattr(ol, 'printed_picking_at') else None,
            "printed_picking_by": ol.printed_picking_by if hasattr(ol, 'printed_picking_by') else None,
        })

    # คำนวณ AllQty ต่อ SKU และจัดสรรตามลำดับความสำคัญ
    sku_total = defaultdict(int)
    for r in rows:
        sku_total[r["sku"]] += r["qty"]
    for r in rows:
        r["allqty"] = sku_total[r["sku"]]

    # จัดลำดับตาม platform priority และเวลา แล้วทำ allocation
    by_sku = defaultdict(list)
    for r in rows:
        by_sku[r["sku"]].append(r)
    for sku, arr in by_sku.items():
        arr.sort(key=lambda x: (PLATFORM_PRIORITY.get(x["platform"], 999), x["order_time"] or now_thai()))
        # สต็อกเริ่มต้น
        stock_qty = arr[0]["stock_qty"]
        for r in arr:
            if r["accepted"]:
                stock_qty -= r["qty"]
                r["allocation_status"] = "ACCEPTED"
                continue
            if stock_qty <= 0:
                r["allocation_status"] = "SHORTAGE"
            elif stock_qty <= 3:
                # ของน้อย (1–3)
                r["allocation_status"] = "LOW_STOCK"
                stock_qty -= r["qty"]  # กันไว้ถ้ากดรับ
            else:
                r["allocation_status"] = "READY_ACCEPT"
                stock_qty -= r["qty"]

    # mark packed
    for r in rows:
        if r["sales_status"] and "ครบตามจำนวน" in r["sales_status"]:
            r["allocation_status"] = "PACKED"

    # KPI
    kpis = {
        "total": len(rows),
        "ready": sum(1 for r in rows if r["allocation_status"]=="READY_ACCEPT"),
        "accepted": sum(1 for r in rows if r["allocation_status"]=="ACCEPTED"),
        "low": sum(1 for r in rows if r["allocation_status"]=="LOW_STOCK"),
        "nostock": sum(1 for r in rows if r["allocation_status"] in ("SHORTAGE",) or r["stock_qty"]<=0),
        "short": sum(1 for r in rows if r["allocation_status"]=="SHORTAGE"),
        "packed": sum(1 for r in rows if r["allocation_status"]=="PACKED"),
    }
    return rows, kpis
