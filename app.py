# app.py
from __future__ import annotations

import os, csv
from datetime import datetime, date, timedelta
from io import BytesIO
from functools import wraps

import pandas as pd
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, send_file, jsonify, session
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func, text
from sqlalchemy.sql import bindparam

from utils import (
    now_thai, to_thai_be, to_be_date_str, TH_TZ, current_be_year,
    normalize_platform, sla_text, compute_due_date
)
from services.lowstock import (
    get_low_stock_df_adapter,
    get_open_order_lines_df_adapter,
    compose_lowstock_report,
)
from models import db, Shop, Product, Stock, Sales, OrderLine, User
from importers import import_products, import_stock, import_sales, import_orders
from allocation import compute_allocation


APP_NAME = os.environ.get("APP_NAME", "VNIX Order Management")


# -----------------------------
# สร้างแอป + บูตระบบเบื้องต้น
# -----------------------------
def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "vnix-secret")

    db_path = os.path.join(os.path.dirname(__file__), "data.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    # =========[ NEW ]=========
    # Model: ออเดอร์ที่ถูกทำเป็น "ยกเลิก"
    class CancelledOrder(db.Model):
        __tablename__ = "cancelled_orders"
        id = db.Column(db.Integer, primary_key=True)
        order_id = db.Column(db.String(128), unique=True, index=True, nullable=False)
        imported_at = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
        imported_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
        note = db.Column(db.String(255))

    # =========[ NEW ]=========  Order "จ่ายงานแล้ว"
    class IssuedOrder(db.Model):
        __tablename__ = "issued_orders"
        id = db.Column(db.Integer, primary_key=True)
        order_id = db.Column(db.String(128), unique=True, index=True, nullable=False)
        issued_at = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
        issued_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
        source = db.Column(db.String(32))  # 'import' | 'print:picking' | 'print:warehouse' | 'manual'
        note = db.Column(db.String(255))
    # =========[ /NEW ]=========

    # ---------- Helper: Table name (OrderLine) ----------
    def _ol_table_name() -> str:
        try:
            return OrderLine.__table__.name
        except Exception:
            return getattr(OrderLine, "__tablename__", "order_lines")

    # ---------- Auto-migrate: ensure print columns exist ----------
    def _ensure_orderline_print_columns():
        """Auto-migrate: เพิ่มคอลัมน์สำหรับติดตามสถานะการพิมพ์ Warehouse และ Picking"""
        tbl = _ol_table_name()
        with db.engine.connect() as con:
            cols = {row[1] for row in con.execute(text(f"PRAGMA table_info({tbl})")).fetchall()}

            def add(col, ddl):
                if col not in cols:
                    con.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col} {ddl}"))

            # สำหรับ "ใบงานคลัง (Warehouse Job Sheet)"
            add("printed_warehouse", "INTEGER DEFAULT 0")  # จำนวนครั้งที่พิมพ์
            add("printed_warehouse_at", "TEXT")  # timestamp ครั้งล่าสุด
            add("printed_warehouse_by", "TEXT")  # username ผู้พิมพ์

            # สำหรับ "Picking List"
            add("printed_picking", "INTEGER DEFAULT 0")  # จำนวนครั้งที่พิมพ์
            add("printed_picking_at", "TEXT")  # timestamp ครั้งล่าสุด
            add("printed_picking_by", "TEXT")  # username ผู้พิมพ์

            # สำหรับ "จ่ายงาน(รอบที่)"
            add("dispatch_round", "INTEGER")

            # สำหรับ "รายงานสินค้าน้อย" (แยกจากคลัง/Picking)
            add("printed_lowstock", "INTEGER DEFAULT 0")
            add("printed_lowstock_at", "TEXT")
            add("printed_lowstock_by", "TEXT")
            add("lowstock_round", "INTEGER")

            # สำหรับ "รายงานไม่มีสินค้า" (แยกจาก lowstock)
            add("printed_nostock", "INTEGER DEFAULT 0")
            add("printed_nostock_at", "TEXT")
            add("printed_nostock_by", "TEXT")
            add("nostock_round", "INTEGER")

            con.commit()

    # ========== [NEW] Auto-migrate shops unique: (platform, name) ==========
    def _has_unique_index_on(conn, table: str, columns_exact: list[str]) -> tuple[bool, str | None]:
        idx_list = conn.execute(text(f"PRAGMA index_list({table})")).fetchall()
        for row in idx_list:
            idx_name = row[1]
            is_unique = int(row[2]) == 1
            if not is_unique:
                continue
            cols = [r[2] for r in conn.execute(text(f"PRAGMA index_info('{idx_name}')")).fetchall()]
            if cols == columns_exact:
                return True, idx_name
        return False, None

    def _migrate_shops_unique_to_platform_name():
        """ย้าย unique จาก name เดี่ยว → เป็น (platform, name)"""
        with db.engine.begin() as con:
            has_composite, _ = _has_unique_index_on(con, "shops", ["platform", "name"])
            if has_composite:
                return
            has_name_unique, idx_name = _has_unique_index_on(con, "shops", ["name"])
            if has_name_unique:
                is_auto = idx_name.startswith("sqlite_autoindex")
                if is_auto:
                    cols_info = con.execute(text("PRAGMA table_info(shops)")).fetchall()
                    col_names = [c[1] for c in cols_info]
                    has_created_at = "created_at" in col_names
                    con.execute(text("ALTER TABLE shops RENAME TO shops_old"))
                    create_sql = """
                    CREATE TABLE shops (
                        id INTEGER PRIMARY KEY,
                        platform TEXT,
                        name TEXT NOT NULL,
                        created_at TEXT
                    )
                    """ if has_created_at else """
                    CREATE TABLE shops (
                        id INTEGER PRIMARY KEY,
                        platform TEXT,
                        name TEXT NOT NULL
                    )
                    """
                    con.execute(text(create_sql))
                    copy_cols = "id, platform, name" + (", created_at" if has_created_at else "")
                    con.execute(text(f"INSERT INTO shops ({copy_cols}) SELECT {copy_cols} FROM shops_old"))
                    con.execute(text("DROP TABLE shops_old"))
                else:
                    con.execute(text(f"DROP INDEX IF EXISTS {idx_name}"))
            con.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_shops_platform_name ON shops(platform, name)"))
    # ========== [/NEW] ==========

    # =========[ NEW ]=========
    def _ensure_issue_table():
        try:
            IssuedOrder.__table__.create(bind=db.engine, checkfirst=True)
        except Exception as e:
            app.logger.warning(f"[issued_orders] ensure table failed: {e}")
    # =========[ /NEW ]=========

    with app.app_context():
        db.create_all()
        _ensure_orderline_print_columns()
        _migrate_shops_unique_to_platform_name()
        _ensure_issue_table()  # <<< NEW
        # bootstrap admin
        if User.query.count() == 0:
            admin = User(
                username="admin",
                password_hash=generate_password_hash("admin123"),
                role="admin",
                active=True
            )
            db.session.add(admin)
            db.session.commit()

    # -----------------
    # Jinja filters
    # -----------------
    @app.template_filter("thai_be")
    def thai_be_filter(dt):
        try:
            return to_thai_be(dt)
        except Exception:
            return ""

    @app.template_filter("be_date")
    def be_date_filter(d):
        try:
            return to_be_date_str(d)
        except Exception:
            return ""

    # -----------------
    # UI context
    # -----------------
    @app.context_processor
    def inject_globals():
        return {
            "APP_NAME": APP_NAME,
            "BE_YEAR": current_be_year(),
            "CURRENT_USER": current_user()
        }

    # ให้ template ตรวจ endpoint ได้ (กันพังค่า has_endpoint)
    @app.template_global()
    def has_endpoint(endpoint: str) -> bool:
        try:
            return endpoint in app.view_functions
        except Exception:
            return False

    # -----------------
    # Auth helpers
    # -----------------
    def current_user():
        uid = session.get("uid")
        if not uid:
            return None
        return db.session.get(User, uid)

    def login_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user():
                return redirect(url_for("login", next=request.path))
            return fn(*args, **kwargs)
        return wrapper

    # -----------------
    # Utilities (app)
    # -----------------
    def parse_date_any(s: str | None):
        if not s:
            return None
        s = s.strip()
        try:
            if "-" in s:
                y, m, d = s.split("-")
                return date(int(y), int(m), int(d))
            else:
                d, m, y = s.split("/")
                y = int(y)
                if y > 2400:
                    y -= 543
                return date(y, int(m), int(d))
        except Exception:
            return None

    def _get_line_sku(line) -> str:
        if hasattr(line, "sku") and line.sku:
            return str(line.sku).strip()
        try:
            prod = getattr(line, "product", None)
            if prod and getattr(prod, "sku", None):
                return str(prod.sku).strip()
        except Exception:
            pass
        return ""

    def _calc_stock_qty_for_line(line: OrderLine) -> int:
        sku = _get_line_sku(line)
        if not sku:
            return 0
        prod = Product.query.filter_by(sku=sku).first()
        if prod and hasattr(prod, "stock_qty"):
            try:
                return int(prod.stock_qty or 0)
            except Exception:
                pass
        st = Stock.query.filter_by(sku=sku).first()
        try:
            return int(st.qty) if st and st.qty is not None else 0
        except Exception:
            return 0

    def _build_allqty_map(rows: list[dict]) -> dict[str, int]:
        total_by_sku: dict[str, int] = {}
        for r in rows:
            sku = (r.get("sku") or "").strip()
            if not sku:
                continue
            total_by_sku[sku] = total_by_sku.get(sku, 0) + int(r.get("qty", 0) or 0)
        return total_by_sku

    def _recompute_allocation_row(r: dict) -> dict:
        stock_qty = int(r.get("stock_qty", 0) or 0)
        allqty = int(r.get("allqty", r.get("qty", 0)) or 0)
        sales_status = (r.get("sales_status") or "").upper()
        packed_flag = bool(r.get("packed", False))
        accepted = bool(r.get("accepted", False))
        order_time = r.get("order_time")
        platform = r.get("platform") or (r.get("shop_platform") if r.get("shop_platform") else "")

        if sales_status == "PACKED" or packed_flag:
            allocation_status = "PACKED"
        elif accepted:
            allocation_status = "ACCEPTED"
        elif stock_qty <= 0:
            allocation_status = "SHORTAGE"
        elif allqty > stock_qty:
            allocation_status = "NOT_ENOUGH"
        elif stock_qty <= 3:
            allocation_status = "LOW_STOCK"
        else:
            allocation_status = "READY_ACCEPT"

        if allocation_status == "PACKED":
            sla = ""
        else:
            try:
                sla = sla_text(platform, order_time) if order_time else ""
            except Exception:
                sla = ""
        try:
            due_date = compute_due_date(platform, order_time) if order_time else None
        except Exception:
            due_date = None

        r["allocation_status"] = allocation_status
        r["sla"] = sla
        r["due_date"] = due_date
        return r

    def _annotate_order_spans(rows: list[dict]) -> list[dict]:
        seen = set()
        for r in rows:
            oid = (r.get("order_id") or "").strip()
            if not oid:
                r["show_order_id"] = True
                r["order_id_display"] = ""
                continue
            if oid in seen:
                r["show_order_id"] = False
                r["order_id_display"] = ""
            else:
                r["show_order_id"] = True
                r["order_id_display"] = oid
                seen.add(oid)
        return rows

    def _group_rows_for_report(rows: list[dict]) -> list[dict]:
        def _key(r):
            return (
                (r.get("order_id") or ""),
                (r.get("platform") or ""),
                (r.get("shop") or ""),
                (r.get("logistic") or ""),
                (r.get("sku") or "")
            )
        rows = sorted(rows, key=_key)
        rows = _annotate_order_spans(rows)

        counts: dict[str, int] = {}
        for r in rows:
            oid = (r.get("order_id") or "").strip()
            counts[oid] = counts.get(oid, 0) + 1

        for r in rows:
            oid = (r.get("order_id") or "").strip()
            r["order_rowspan"] = counts.get(oid, 1) if r.get("show_order_id") else 0
            r["order_id_display"] = oid if r.get("show_order_id") else ""
        return rows

    def _group_rows_for_warehouse_report(rows: list[dict]) -> list[dict]:
        """Group rows by order_id to show only 1 row per order for warehouse report"""
        order_map = {}
        
        for r in rows:
            oid = (r.get("order_id") or "").strip()
            if not oid:
                continue
            
            if oid not in order_map:
                # First row for this order - keep it
                # ใช้ printed_warehouse_count หรือ printed_count ที่มาจาก DB (ไม่ใช่ printed_warehouse ที่เป็น 0 ตลอด)
                order_map[oid] = {
                    "order_id": oid,
                    "platform": r.get("platform", ""),
                    "shop": r.get("shop", ""),
                    "logistic": r.get("logistic", ""),
                    "accepted_by": r.get("accepted_by", ""),
                    "printed_count": r.get("printed_warehouse_count") or r.get("printed_count") or r.get("printed_warehouse") or 0,
                    "printed_warehouse": r.get("printed_warehouse_count") or r.get("printed_count") or r.get("printed_warehouse") or 0,
                    "printed_warehouse_at": r.get("printed_warehouse_at"),
                    "printed_warehouse_by": r.get("printed_warehouse_by"),
                    "dispatch_round": r.get("dispatch_round"),
                }
        
        # Convert back to list and sort
        result = list(order_map.values())
        result.sort(key=lambda r: (r["platform"], r["shop"], r["order_id"]))
        return result

    # -----------------
    # สร้างเซ็ต Order พร้อมรับทั้งออเดอร์ / สินค้าน้อยทั้งออเดอร์
    # -----------------
    def _orders_ready_set(rows: list[dict]) -> set[str]:
        by_oid: dict[str, list[dict]] = {}
        for r in rows:
            oid = (r.get("order_id") or "").strip()
            if not oid:
                continue
            by_oid.setdefault(oid, []).append(r)

        ready = set()
        for oid, items in by_oid.items():
            if not items:
                continue
            all_ready = True
            for it in items:
                status = (it.get("allocation_status") or "").upper()
                accepted = bool(it.get("accepted", False))
                packed = (status == "PACKED") or bool(it.get("packed", False))
                if not (status == "READY_ACCEPT" and not accepted and not packed):
                    all_ready = False
                    break
            if all_ready:
                ready.add(oid)
        return ready

    def _orders_lowstock_order_set(rows: list[dict]) -> set[str]:
        by_oid: dict[str, list[dict]] = {}
        for r in rows:
            oid = (r.get("order_id") or "").strip()
            if not oid:
                continue
            by_oid.setdefault(oid, []).append(r)

        result = set()
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
                result.add(oid)
        return result

    # ===========================================================
    # Packed helpers — จาก "เปิดใบขายครบตามจำนวนแล้ว"
    # ===========================================================
    def _is_line_opened_full(r: dict) -> bool:
        text_pool = [
            str(r.get("sale_status") or ""),
            str(r.get("sale_text") or ""),
            str(r.get("sales_status") or ""),
            str(r.get("sales_note") or ""),
        ]
        norm = " ".join(s.strip() for s in text_pool if s).lower()
        flag = bool(r.get("sale_open_full") or r.get("opened_full") or r.get("is_opened_full"))
        return flag or ("เปิดใบขายครบตามจำนวนแล้ว" in norm) or ("opened_full" in norm)

    def _orders_packed_set(rows: list[dict]) -> set[str]:
        by_oid: dict[str, list[dict]] = {}
        for r in rows:
            oid = (r.get("order_id") or "").strip()
            if not oid:
                continue
            by_oid.setdefault(oid, []).append(r)
        packed: set[str] = set()
        for oid, items in by_oid.items():
            if items and all(_is_line_opened_full(it) for it in items):
                packed.add(oid)
        return packed

    # ---------------------------------------------------------
    # ฟังก์ชัน DB raw (ใช้เพราะคอลัมน์ถูกเพิ่มแบบ ALTER TABLE)
    # ---------------------------------------------------------
    def _detect_already_printed(oids: list[str], kind: str) -> set[str]:
        if not oids:
            return set()
        tbl = _ol_table_name()
        col = "printed_warehouse" if kind == "warehouse" else "printed_picking"
        # เปลี่ยนจาก =1 เป็น >=1 เพื่อกันกรณีพิมพ์หลายครั้ง
        sql = text(f"SELECT DISTINCT order_id FROM {tbl} WHERE order_id IN :oids AND {col} >= 1")
        sql = sql.bindparams(bindparam("oids", expanding=True))
        rows = db.session.execute(sql, {"oids": oids}).scalars().all()
        return set(r for r in rows if r)

    def _mark_printed(oids: list[str], kind: str, user_id: int | None, when_iso: str):
        """อัปเดตสถานะการพิมพ์ + timestamp + username"""
        if not oids:
            return
        
        # ดึง username จาก user_id
        username = None
        if user_id:
            user_obj = User.query.get(user_id)
            if user_obj:
                username = user_obj.username
        
        tbl = _ol_table_name()
        if kind == "warehouse":
            col_count = "printed_warehouse"
            col_at   = "printed_warehouse_at"
            col_by   = "printed_warehouse_by"
        else:
            col_count = "printed_picking"
            col_at   = "printed_picking_at"
            col_by   = "printed_picking_by"

        sql = text(
            f"""
            UPDATE {tbl}
               SET {col_count}=COALESCE({col_count},0)+1,
                   {col_at}=:ts,
                   {col_by}=:username
             WHERE order_id IN :oids
            """
        ).bindparams(bindparam("oids", expanding=True))
        db.session.execute(sql, {"username": username, "ts": when_iso, "oids": oids})
        db.session.commit()

    # --------------------------
    # Print count helpers (ใหม่)
    # --------------------------
    def _get_print_counts_local(oids: list[str], kind: str) -> dict[str, int]:
        """คืน dict: {order_id: count} อ่านจำนวนครั้งที่พิมพ์จากคอลัมน์ printed_warehouse หรือ printed_picking หรือ printed_lowstock"""
        if not oids:
            return {}
        tbl = _ol_table_name()
        if kind == "lowstock":
            col = "printed_lowstock"
        elif kind == "nostock":  # <<< เพิ่มสำหรับรายงานไม่มีสินค้า
            col = "printed_nostock"
        elif kind == "warehouse":
            col = "printed_warehouse"
        else:
            col = "printed_picking"
        sql = text(f"SELECT order_id, COALESCE(MAX({col}),0) AS c FROM {tbl} WHERE order_id IN :oids GROUP BY order_id")
        sql = sql.bindparams(bindparam("oids", expanding=True))
        rows_sql = db.session.execute(sql, {"oids": oids}).all()
        return {str(r[0]): int(r[1] or 0) for r in rows_sql if r and r[0]}

    def _mark_lowstock_printed(oids: list[str], username: str | None, when_iso: str):
        """อัปเดตการพิมพ์สำหรับรายงานสินค้าน้อย"""
        if not oids:
            return
        tbl = _ol_table_name()
        sql = text(f"""
            UPDATE {tbl}
               SET printed_lowstock=COALESCE(printed_lowstock,0)+1,
                   printed_lowstock_at=:ts,
                   printed_lowstock_by=:byu
             WHERE order_id IN :oids
        """).bindparams(bindparam("oids", expanding=True))
        db.session.execute(sql, {"ts": when_iso, "byu": username, "oids": oids})
        db.session.commit()

    def _mark_nostock_printed(oids: list[str], username: str | None, when_iso: str):
        """อัปเดตการพิมพ์สำหรับรายงานไม่มีสินค้า"""
        if not oids:
            return
        tbl = _ol_table_name()
        sql = text(f"""
            UPDATE {tbl}
               SET printed_nostock=COALESCE(printed_nostock,0)+1,
                   printed_nostock_at=:ts,
                   printed_nostock_by=:byu
             WHERE order_id IN :oids
        """).bindparams(bindparam("oids", expanding=True))
        db.session.execute(sql, {"ts": when_iso, "byu": username, "oids": oids})
        db.session.commit()

    def _inject_print_counts_to_rows(rows: list[dict], kind: str):
        """ฝัง printed_*_count และ printed_*_at ลงในแต่ละแถว (ใช้กับ Warehouse report)"""
        oids = sorted({(r.get("order_id") or "").strip() for r in rows if r.get("order_id")})
        counts = _get_print_counts_local(oids, kind)
        
        # Also get the timestamp of last print
        if not oids:
            return
        
        tbl = _ol_table_name()
        col_at = "printed_warehouse_at" if kind == "warehouse" else "printed_picking_at"
        sql = text(f"SELECT order_id, MAX({col_at}) AS last_printed_at FROM {tbl} WHERE order_id IN :oids GROUP BY order_id")
        sql = sql.bindparams(bindparam("oids", expanding=True))
        rows_sql = db.session.execute(sql, {"oids": oids}).all()
        timestamps = {}
        
        # Convert ISO string to datetime object
        for r_sql in rows_sql:
            if r_sql and r_sql[0] and r_sql[1]:
                try:
                    # Parse ISO datetime string
                    dt = datetime.fromisoformat(r_sql[1])
                    if dt.tzinfo is None:
                        dt = TH_TZ.localize(dt)
                    timestamps[str(r_sql[0])] = dt
                except Exception:
                    pass
        
        for r in rows:
            oid = (r.get("order_id") or "").strip()
            c = int(counts.get(oid, 0))
            r["printed_count"] = c
            if kind == "warehouse":
                r["printed_warehouse_count"] = c
                r["printed_warehouse"] = c  # <-- เพิ่มบรรทัดนี้เพื่อให้เทมเพลตอ่ยใช้ได้
                r["printed_warehouse_at"] = timestamps.get(oid)
            else:
                r["printed_picking_count"] = c
                r["printed_picking"] = c  # <-- และบรรทัดน้
                r["printed_picking_at"] = timestamps.get(oid)

    # =========[ NEW ]=========
    # ส่วนเสริมเพื่อ "Order ยกเลิก"
    try:
        from openpyxl import load_workbook, Workbook
        _OPENPYXL_OK = True
    except Exception:
        _OPENPYXL_OK = False

    def _ensure_cancel_table():
        try:
            CancelledOrder.__table__.create(bind=db.engine, checkfirst=True)
        except Exception as e:
            app.logger.warning(f"[cancelled_orders] ensure table failed: {e}")

    def _cancelled_oids_set() -> set[str]:
        rows = db.session.query(CancelledOrder.order_id).all()
        return {r[0] for r in rows if r and r[0]}

    def _filter_out_cancelled_rows(rows: list[dict]) -> list[dict]:
        canc = _cancelled_oids_set()
        if not canc:
            return rows
        res = []
        for r in rows:
            oid = (r.get("order_id") or "").strip()
            if oid and oid in canc:
                continue
            res.append(r)
        return res

    # ===== HELPER: Issued (จ่ายงานแล้ว) =====
    def _issued_oids_set() -> set[str]:
        rows = db.session.query(IssuedOrder.order_id).all()
        return {r[0] for r in rows if r and r[0]}

    def _filter_out_issued_rows(rows: list[dict]) -> list[dict]:
        issued = _issued_oids_set()
        if not issued:
            return rows
        res = []
        for r in rows:
            oid = (r.get("order_id") or "").strip()
            if oid and oid in issued:
                continue
            res.append(r)
        return res

    # ===== HELPER: Low Stock Printed (พิมพ์รายงานสินค้าน้อยแล้ว) =====
    def _lowstock_printed_oids_set() -> set[str]:
        """ดึง order_id ที่เคยพิมพ์รายงานสินค้าน้อยแล้ว"""
        tbl = _ol_table_name()
        rows = db.session.execute(text(f"""
            SELECT DISTINCT order_id
            FROM {tbl}
            WHERE printed_lowstock > 0
        """)).fetchall()
        return {r[0] for r in rows if r and r[0]}

    def _filter_out_lowstock_printed_rows(rows: list[dict]) -> list[dict]:
        """กรองออเดอร์ที่พิมพ์รายงานสินค้าน้อยออกแล้ว (ข้อ 2)"""
        printed = _lowstock_printed_oids_set()
        if not printed:
            return rows
        res = []
        for r in rows:
            oid = (r.get("order_id") or "").strip()
            if oid and oid in printed:
                continue
            res.append(r)
        return res

    def _mark_issued(oids: list[str], user_id: int | None, source: str = "manual", when_dt=None):
        """ทำเครื่องหมาย 'จ่ายงานแล้ว' โดยไม่แก้ทับข้อมูลเก่า (ยึดเวลาเดิม)"""
        if not oids:
            return 0
        # ใช้เวลาที่ส่งมา (เช่น ตอน import) ถ้าไม่ส่งมาก็ใช้เวลาปัจจุบันโซนไทย
        when_dt = when_dt or now_thai()
        try:
            # เก็บแบบ naive เพื่อให้ SQLite รับได้
            if getattr(when_dt, "tzinfo", None) is not None:
                when_dt = when_dt.replace(tzinfo=None)
        except Exception:
            pass

        existing = {
            r[0] for r in db.session.query(IssuedOrder.order_id)
            .filter(IssuedOrder.order_id.in_(oids)).distinct().all()
        }
        inserted = 0
        for oid in oids:
            oid = (oid or "").strip()
            if not oid or oid in existing:
                # มีข้อมูลเก่าแล้ว (เช่นมาจากการพิมพ์) ก็ไม่แก้ทับ ⇒ ยึดเวลาเก่าไว้
                continue
            db.session.add(IssuedOrder(order_id=oid, issued_at=when_dt, issued_by_user_id=user_id, source=source))
            inserted += 1
        db.session.commit()
        return inserted

    def _unissue(oids: list[str]) -> int:
        if not oids:
            return 0
        n = db.session.query(IssuedOrder).filter(IssuedOrder.order_id.in_(oids)).delete(synchronize_session=False)
        db.session.commit()
        return n

    # ให้ import "จ่ายงานแล้ว" ตั้งค่า counter ขั้นต่ำเป็น 1
    def _ensure_min_print_count(oids: list[str], min_count: int = 1, user_id: int | None = None, when_iso: str | None = None):
        """บังคับให้ printed_picking_count >= min_count (เฉพาะ Picking เท่านั้น)"""
        if not oids:
            return
        tbl = _ol_table_name()
        when_iso = when_iso or now_thai().isoformat()

        # เซ็ตเฉพาะ Picking (ไม่แตะ Warehouse)
        sql = text(f"""
            UPDATE {tbl}
               SET printed_picking=1,
                   printed_picking_count = CASE WHEN COALESCE(printed_picking_count,0) < :mc THEN :mc ELSE printed_picking_count END,
                   printed_picking_by_user_id = COALESCE(printed_picking_by_user_id, :uid),
                   printed_picking_at = COALESCE(printed_picking_at, :ts)
             WHERE order_id IN :oids
        """).bindparams(bindparam("oids", expanding=True))
        db.session.execute(sql, {"mc": min_count, "uid": user_id, "ts": when_iso, "oids": oids})

        db.session.commit()

    def _ensure_shops_from_df(df, platform: str, default_shop_name: str = None):
        """สร้างหรือใช้ Shop ที่มีอยู่แล้ว ก่อนที่จะ import orders (กัน UNIQUE constraint พัง)"""
        from utils import normalize_platform
        platform = normalize_platform(platform)
        
        # รวบรวม shop names ที่มีใน df (ลองดูหลายคอลัมน์ที่อาจมีชื่อร้าน)
        shop_names = set()
        for col in df.columns:
            col_lower = str(col).lower()
            if "shop" in col_lower or "ร้าน" in col_lower:
                for val in df[col].dropna().unique():
                    name = str(val).strip()
                    if name:
                        shop_names.add(name)
        
        # ถ้าไม่เจอใน df ให้ใช้ default_shop_name
        if not shop_names and default_shop_name:
            shop_names.add(default_shop_name.strip())
        
        # สร้าง/ใช้ shop ที่มีอยู่แล้ว
        for name in shop_names:
            existing = Shop.query.filter_by(platform=platform, name=name).first()
            if not existing:
                new_shop = Shop(platform=platform, name=name)
                db.session.add(new_shop)
        db.session.commit()

    def _parse_order_ids_from_upload(file_storage) -> list[str]:
        filename = (file_storage.filename or "").lower()
        data = file_storage.read()
        file_storage.stream.seek(0)

        order_ids: list[str] = []

        # Excel
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            if not _OPENPYXL_OK:
                raise RuntimeError("ไม่พบไลบรารี openpyxl สำหรับอ่านไฟล์ Excel, ติดตั้งด้วย: pip install openpyxl")
            wb = load_workbook(filename=BytesIO(data), read_only=True, data_only=True)
            ws = wb.active
            for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if not row:
                    continue
                val = row[0]
                if i == 1 and isinstance(val, str) and val.strip().lower() in {"order_id", "order_no", "เลขออเดอร์"}:
                    continue
                if val is None:
                    continue
                s = str(val).strip()
                if s:
                    order_ids.append(s)
            return order_ids

        # CSV
        if filename.endswith(".csv"):
            text_data = data.decode("utf-8-sig", errors="ignore")
            reader = csv.reader(text_data.splitlines())
            for i, row in enumerate(reader, start=1):
                if not row:
                    continue
                val = row[0]
                if i == 1 and isinstance(val, str) and val.strip().lower() in {"order_id", "order_no", "เลขออเดอร์"}:
                    continue
                s = str(val).strip()
                if s:
                    order_ids.append(s)
            return order_ids

        raise RuntimeError("รองรับเฉพาะไฟล์ .xlsx .xls หรือ .csv เท่านั้น")
    # =========[ /NEW ]=========

    # -------------
    # Routes: Auth & Users
    # -------------

    # --------- Admin: Shops (เดิม) ---------
    @app.route("/admin/shops")
    @login_required
    def admin_shops():
        cu = current_user()
        if not cu or cu.role not in {"admin", "staff"}:
            flash("ต้องเป็นผู้ดูแลระบบหรือพนักงานเท่านั้น", "danger")
            return redirect(url_for("dashboard"))
        shops = Shop.query.order_by(Shop.platform.asc(), Shop.name.asc()).all()
        counts = {s.id: db.session.query(func.count(OrderLine.id)).filter_by(shop_id=s.id).scalar() for s in shops}
        return render_template("admin_shops.html", shops=shops, counts=counts)

    @app.route("/admin/shops/<int:shop_id>/delete", methods=["POST"])
    @login_required
    def delete_shop(shop_id):
        cu = current_user()
        if not cu or cu.role != "admin":
            flash("เฉพาะแอดมินเท่านั้นที่ลบได้", "danger")
            return redirect(url_for("admin_shops"))
        s = Shop.query.get(shop_id)
        if not s:
            flash("ไม่พบร้านนี้", "warning")
            return redirect(url_for("admin_shops"))
        cnt = db.session.query(func.count(OrderLine.id)).filter_by(shop_id=s.id).scalar()
        if cnt and cnt > 0:
            flash("ไม่สามารถลบได้: มีออเดอร์ผูกกับร้านนี้อยู่", "danger")
            return redirect(url_for("admin_shops"))
        db.session.delete(s)
        db.session.commit()
        flash(f"ลบร้าน '{s.name}' แล้ว", "success")
        return redirect(url_for("admin_shops"))
    # --------------------------------------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            u = User.query.filter_by(username=username, active=True).first()
            if u and check_password_hash(u.password_hash, password):
                session["uid"] = u.id
                flash("เข้าสู่ระบบสำเร็จ", "success")
                return redirect(request.args.get("next") or url_for("dashboard"))
            flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", "danger")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("ออกจากระบบแล้ว", "info")
        return redirect(url_for("login"))

    @app.route("/admin/users", methods=["GET", "POST"])
    @login_required
    def admin_users():
        cu = current_user()
        if cu.role != "admin":
            flash("ต้องเป็นผู้ดูแลระบบเท่านั้น", "danger")
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            action = request.form.get("action")
            if action == "create":
                username = request.form.get("username").strip()
                password = request.form.get("password")
                role = request.form.get("role", "user")
                if not username or not password:
                    flash("กรุณากรอกชื่อผู้ใช้/รหัสผ่าน", "danger")
                elif User.query.filter_by(username=username).first():
                    flash("มีชื่อผู้ใช้นี้อยู่แล้ว", "warning")
                else:
                    u = User(
                        username=username,
                        password_hash=generate_password_hash(password),
                        role=role,
                        active=True
                    )
                    db.session.add(u)
                    db.session.commit()
                    flash(f"สร้างผู้ใช้ {username} แล้ว", "success")
            elif action == "delete":
                uid = int(request.form.get("uid"))
                if uid == cu.id:
                    flash("ลบตัวเองไม่ได้", "warning")
                else:
                    User.query.filter_by(id=uid).delete()
                    db.session.commit()
                    flash("ลบผู้ใช้แล้ว", "success")
        users = User.query.order_by(User.created_at.desc()).all() if hasattr(User, "created_at") else User.query.all()
        return render_template("users.html", users=users)

    # -------------
    # Dashboard
    # -------------
    @app.route("/")
    @login_required
    def dashboard():
        platform = normalize_platform(request.args.get("platform"))
        shop_id = request.args.get("shop_id")
        import_date_str = request.args.get("import_date")
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")
        status = request.args.get("status")

        shops = Shop.query.order_by(Shop.name.asc()).all()

        filters = {
            "platform": platform if platform else None,
            "shop_id": int(shop_id) if shop_id else None,
            "import_date": parse_date_any(import_date_str),
            "date_from": datetime.combine(parse_date_any(date_from), datetime.min.time(), tzinfo=TH_TZ) if date_from else None,
            "date_to": datetime.combine(parse_date_any(date_to) + timedelta(days=1), datetime.min.time(), tzinfo=TH_TZ) if date_to else None,
        }

        rows, _kpis_from_allocation = compute_allocation(db.session, filters)

        # --- ตัดออเดอร์ที่ยกเลิกออก ---
        rows = _filter_out_cancelled_rows(rows)
        rows = _filter_out_issued_rows(rows)   # <<< NEW: ไม่นับออเดอร์ที่จ่ายงานแล้วใน Dashboard หลัก

        for r in rows:
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try:
                            stock_qty = int(prod.stock_qty or 0)
                        except Exception:
                            stock_qty = 0
                    else:
                        st = Stock.query.filter_by(sku=sku).first()
                        stock_qty = int(st.qty) if st and st.qty is not None else 0
                r["stock_qty"] = stock_qty
            r["accepted"] = bool(r.get("accepted", False))
            r["sales_status"] = r.get("sales_status", None)
            r["logistic"] = r.get("logistic") or r.get("logistic_type") or "-"

        totals = _build_allqty_map(rows)
        for r in rows:
            r["allqty"] = int(totals.get((r.get("sku") or "").strip(), r.get("qty", 0)) or 0)
        rows = [_recompute_allocation_row(r) for r in rows]

        packed_oids = _orders_packed_set(rows)
        for r in rows:
            if (r.get("order_id") or "").strip() in packed_oids:
                r["allocation_status"] = "PACKED"
                r["packed"] = True
                r["actions_disabled"] = True
            else:
                r["actions_disabled"] = False

        orders_ready = _orders_ready_set(rows)
        orders_low_order = _orders_lowstock_order_set(rows)
        status_norm = (status or "").strip().upper()

        if status_norm in {"PACKED"}:
            rows = [r for r in rows if (r.get("order_id") or "").strip() in packed_oids]
        else:
            rows = [r for r in rows if (r.get("order_id") or "").strip() not in packed_oids]
            if status_norm == "ORDER_READY":
                rows = [r for r in rows if (r.get("order_id") or "").strip() in orders_ready]
            elif status_norm in {"ORDER_LOW_STOCK", "ORDER_LOW"}:
                rows = [r for r in rows if (r.get("order_id") or "").strip() in orders_low_order]
            elif status_norm:
                rows = [r for r in rows if (r.get("allocation_status") or "").strip().upper() == status_norm]

        def _sort_key(r):
            return ((r.get("order_id") or ""), (r.get("platform") or ""), (r.get("shop") or ""), (r.get("sku") or ""))
        rows = sorted(rows, key=_sort_key)

        kpis = {
            "ready":     sum(1 for r in rows if r["allocation_status"] == "READY_ACCEPT"),
            "accepted":  sum(1 for r in rows if r["allocation_status"] == "ACCEPTED"),
            "low":       sum(1 for r in rows if r["allocation_status"] == "LOW_STOCK"),
            "nostock":   sum(1 for r in rows if r["allocation_status"] == "SHORTAGE"),
            "notenough": sum(1 for r in rows if r["allocation_status"] == "NOT_ENOUGH"),
            "packed":    len(packed_oids),
            "total_items": len(rows),
            "total_qty":   sum(int(r.get("qty", 0) or 0) for r in rows),
            "orders_unique": len({(r.get("order_id") or "").strip() for r in rows if r.get("order_id")}),
            "orders_ready": len(orders_ready),
            "orders_low":   len(orders_low_order),
        }

        # นับจำนวน SKU ที่มีสินค้าน้อย (ใช้ service กลาง)
        from services.lowstock_queue import count_lowstock_skus
        lowstock_skus_count = count_lowstock_skus(rows)

        # นับจำนวน Order ที่จ่ายงานแล้ว
        issued_count = db.session.query(func.count(IssuedOrder.id)).scalar()

        return render_template(
            "dashboard.html",
            rows=rows,
            shops=shops,
            platform_sel=platform,
            shop_sel=shop_id,
            import_date_sel=import_date_str,
            status_sel=status,
            date_from_sel=date_from,
            date_to_sel=date_to,
            kpis=kpis,
            packed_oids=packed_oids,
            issued_count=issued_count,
            lowstock_skus_count=lowstock_skus_count,  # <<< NEW: ส่งไปแสดงใน badge
        )

    # =========[ NEW ]=========  กดรับ Order ในหน้า Dashboard
    @app.post("/dashboard/accept_order")
    @login_required
    def dashboard_accept_order():
        cu = current_user()
        if not cu:
            flash("กรุณาเข้าสู่ระบบก่อน", "danger")
            return redirect(url_for("login"))

        order_id = request.form.get("order_id")
        sku = request.form.get("sku")
        platform = request.form.get("platform")
        shop_id = request.form.get("shop_id")

        if not order_id or not sku:
            flash("ข้อมูลไม่ครบถ้วน", "danger")
            return redirect(url_for("dashboard"))

        # อัปเดท OrderLine ให้เป็น accepted
        try:
            ol = OrderLine.query.filter_by(order_id=order_id, sku=sku).first()
            if ol:
                ol.accepted = True
                ol.accepted_at = now_thai()
                ol.accepted_by_user_id = cu.id
                ol.accepted_by_username = cu.username
                db.session.commit()
                flash(f"รับออเดอร์ {order_id} (SKU: {sku}) สำเร็จ", "success")
            else:
                flash("ไม่พบรายการที่ต้องการรับ", "warning")
        except Exception as e:
            db.session.rollback()
            app.logger.exception("Accept order failed")
            flash(f"เกิดข้อผิดพลาด: {e}", "danger")

        # redirect กลับไปหน้าเดิมพร้อมฟิลเตอร์
        return redirect(url_for("dashboard", platform=platform, shop_id=shop_id))
    # =========[ /NEW ]=========

    # -----------------------
    # Import endpoints
    # -----------------------
    @app.route("/import/orders", methods=["GET", "POST"])
    @login_required
    def import_orders_view():
        if request.method == "POST":
            platform = request.form.get("platform")
            shop_name = request.form.get("shop_name")
            f = request.files.get("file")
            if not platform or not f:
                flash("กรุณาเลือกแพลตฟอร์ม และเลือกไฟล์", "danger")
                return redirect(url_for("import_orders_view"))
            try:
                df = pd.read_excel(f)
                # >>> สร้าง/ใช้ร้านเดิมก่อนเสมอ (กัน UNIQUE พัง)
                _ensure_shops_from_df(df, platform=platform, default_shop_name=shop_name)
                # เรียก importer เดิม
                imported, updated = import_orders(
                    df, platform=platform, shop_name=shop_name, import_date=now_thai().date()
                )
                flash(f"นำเข้าออเดอร์สำเร็จ: เพิ่ม {imported} อัปเดต {updated}", "success")
                return redirect(url_for("dashboard", import_date=now_thai().date().isoformat()))
            except Exception as e:
                db.session.rollback()
                flash(f"เกิดข้อผิดพลาดในการนำเข้าออเดอร์: {e}", "danger")
                return redirect(url_for("import_orders_view"))
        shops = Shop.query.order_by(Shop.name.asc()).all()
        return render_template("import_orders.html", shops=shops)

    # =========[ NEW ]=========
    # Import Orders ยกเลิก + Template
    @app.route("/import/cancel/template")
    @login_required
    def import_cancel_template():
        fmt = (request.args.get("format") or "xlsx").lower()
        sample = ["ORDER-001", "ORDER-002", "ORDER-ABC-003"]

        if fmt == "xlsx" and _OPENPYXL_OK:
            wb = Workbook()
            ws = wb.active
            ws.title = "cancelled_orders"
            ws["A1"] = "order_id"
            for i, no in enumerate(sample, start=2):
                ws[f"A{i}"] = no
            bio = BytesIO()
            wb.save(bio)
            bio.seek(0)
            return send_file(
                bio,
                as_attachment=True,
                download_name="template_import_orders_cancel.xlsx",
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        # Fallback CSV
        csv_io = BytesIO()
        csv_io.write(("order_id\n" + "\n".join(sample)).encode("utf-8-sig"))
        csv_io.seek(0)
        return send_file(
            csv_io,
            as_attachment=True,
            download_name="template_import_orders_cancel.csv",
            mimetype="text/csv",
        )

    @app.route("/import/cancel", methods=["GET", "POST"])
    @login_required
    def import_cancel_orders():
        _ensure_cancel_table()

        cu = current_user()
        if not cu or cu.role not in {"admin", "staff"}:
            flash("ต้องเป็นผู้ดูแลระบบหรือพนักงานเท่านั้น", "danger")
            return redirect(url_for("dashboard"))

        result = None
        if request.method == "POST":
            f = request.files.get("file")
            if not f or (f.filename or "").strip() == "":
                flash("โปรดเลือกไฟล์ Excel/CSV ก่อน", "warning")
                return redirect(url_for("import_cancel_orders"))
            try:
                order_ids_raw = _parse_order_ids_from_upload(f)
                order_ids = [s.strip() for s in order_ids_raw if s and s.strip()]
                order_ids = list(dict.fromkeys(order_ids))  # unique (คงลำดับ)

                # จับคู่ว่ามีอยู่จริงใน OrderLine
                exists_set = {
                    r[0] for r in db.session.query(OrderLine.order_id)
                    .filter(OrderLine.order_id.in_(order_ids)).distinct().all()
                }
                not_found = [s for s in order_ids if s not in exists_set]

                # เพิ่มเข้า cancelled_orders (กันซ้ำ)
                inserted = 0
                skipped_already = 0
                now = datetime.utcnow()
                for oid in exists_set:
                    existed = CancelledOrder.query.filter_by(order_id=oid).first()
                    if existed:
                        skipped_already += 1
                        continue
                    db.session.add(CancelledOrder(order_id=oid, imported_at=now, imported_by_user_id=cu.id))
                    inserted += 1
                db.session.commit()

                result = {
                    "total_in_file": len(order_ids),
                    "matched_in_system": len(exists_set),
                    "inserted": inserted,
                    "already_cancelled": skipped_already,
                    "not_found": not_found[:50],
                    "preview": order_ids[:10],
                }
                flash(f"บันทึกออเดอร์ยกเลิก {inserted} รายการ (ข้ามซ้ำ {skipped_already})", "success")

            except Exception as e:
                db.session.rollback()
                app.logger.exception("Import cancelled orders failed")
                flash(f"เกิดข้อผิดพลาด: {e}", "danger")
                result = None

        return render_template("import_cancel.html", result=result)

    # =========[ NEW ]=========  Import Orders (จ่ายงานแล้ว)
    @app.route("/import/issued/template")
    @login_required
    def import_issued_template():
        # ใช้ logic เดียวกับ template ของ cancel (คืนไฟล์คอลัมน์ order_id)
        sample = ["ORDER-001", "ORDER-002", "ORDER-003"]
        try:
            from openpyxl import Workbook
            wb = Workbook(); ws = wb.active; ws.title = "issued_orders"; ws["A1"] = "order_id"
            for i, no in enumerate(sample, start=2): ws[f"A{i}"] = no
            bio = BytesIO(); wb.save(bio); bio.seek(0)
            return send_file(bio, as_attachment=True, download_name="template_import_orders_issued.xlsx",
                             mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception:
            csv_io = BytesIO()
            csv_io.write(("order_id\n" + "\n".join(sample)).encode("utf-8-sig"))
            csv_io.seek(0)
            return send_file(csv_io, as_attachment=True, download_name="template_import_orders_issued.csv", mimetype="text/csv")

    @app.route("/import/issued", methods=["GET", "POST"])
    @login_required
    def import_issued_orders():
        cu = current_user()
        if not cu or cu.role not in {"admin", "staff"}:
            flash("ต้องเป็นผู้ดูแลระบบหรือพนักงานเท่านั้น", "danger")
            return redirect(url_for("dashboard"))

        result = None
        if request.method == "POST":
            f = request.files.get("file")
            if not f or (f.filename or "").strip() == "":
                flash("โปรดเลือกไฟล์ Excel/CSV ก่อน", "warning")
                return redirect(url_for("import_issued_orders"))
            try:
                order_ids_raw = _parse_order_ids_from_upload(f)
                order_ids = [s.strip() for s in order_ids_raw if s and s.strip()]
                order_ids = list(dict.fromkeys(order_ids))  # unique + preserve order

                # มีอยู่จริงในระบบ?
                exists_set = {
                    r[0] for r in db.session.query(OrderLine.order_id)
                    .filter(OrderLine.order_id.in_(order_ids)).distinct().all()
                }
                not_found = [s for s in order_ids if s not in exists_set]

                # mark เป็น "จ่ายงานแล้ว" พร้อมบันทึกเวลา import
                imported_at = now_thai()
                inserted = _mark_issued(list(exists_set), user_id=cu.id, source="import", when_dt=imported_at)

                # ตาม requirement: ถ้ายังไม่เคยนับพิมพ์ ให้ตั้งเป็น 1
                if exists_set:
                    _ensure_min_print_count(list(exists_set), min_count=1, user_id=cu.id, when_iso=now_thai().isoformat())

                result = {
                    "total_in_file": len(order_ids),
                    "matched_in_system": len(exists_set),
                    "inserted_issued": inserted,
                    "not_found": not_found[:50],
                }
                flash(f"ทำเครื่องหมาย 'จ่ายงานแล้ว' {inserted} ออเดอร์", "success")

            except Exception as e:
                db.session.rollback()
                app.logger.exception("Import issued orders failed")
                flash(f"เกิดข้อผิดพลาด: {e}", "danger")
                result = None

        return render_template("import_issued.html", result=result)
    # =========[ /NEW ]=========

    @app.route("/dashboard/cancelled")
    @login_required
    def dashboard_cancelled():
        # สร้างตารางถ้ายังไม่มี (จากแพตช์ Import Orders ยกเลิก)
        try:
            CancelledOrder.__table__.create(bind=db.engine, checkfirst=True)
        except Exception:
            pass

        if not current_user():
            return redirect(url_for("login"))

        # รับพารามิเตอร์กรอง
        q = (request.args.get("q") or "").strip()
        platform_sel = normalize_platform(request.args.get("platform"))
        shop_sel = request.args.get("shop_id")
        shop_sel = int(shop_sel) if shop_sel and str(shop_sel).isdigit() else None

        # สรุปแพลตฟอร์มสำหรับตัวเลือก
        platforms = [
            p for (p,) in db.session.query(Shop.platform)
            .filter(Shop.platform.isnot(None))
            .distinct().order_by(Shop.platform.asc()).all()
        ]

        # รายชื่อร้านสำหรับตัวเลือก (ถ้ามีเลือกแพลตฟอร์มจะกรองให้)
        shop_query = Shop.query
        if platform_sel:
            shop_query = shop_query.filter(Shop.platform == platform_sel)
        shops = shop_query.order_by(Shop.name.asc()).all()

        # subquery: map เลข Order -> (shop_id, platform, shop_name) จาก OrderLine -> Shop
        sub = (
            db.session.query(
                OrderLine.order_id.label("oid"),
                func.min(OrderLine.shop_id).label("shop_id"),
                func.min(Shop.platform).label("platform"),
                func.min(Shop.name).label("shop_name"),
            )
            .outerjoin(Shop, Shop.id == OrderLine.shop_id)
            .group_by(OrderLine.order_id)
            .subquery()
        )

        # query หลัก: รายการที่ถูกยกเลิก + join ข้อมูลร้าน/แพลตฟอร์ม
        qry = (
            db.session.query(
                CancelledOrder.order_id,
                sub.c.platform,
                sub.c.shop_name,
                sub.c.shop_id,
            )
            .outerjoin(sub, sub.c.oid == CancelledOrder.order_id)
        )

        # กรองตามคำค้น, แพลตฟอร์ม, ร้าน
        if q:
            qry = qry.filter(CancelledOrder.order_id.contains(q))
        if platform_sel:
            qry = qry.filter(sub.c.platform == platform_sel)
        if shop_sel:
            qry = qry.filter(sub.c.shop_id == shop_sel)

        rows = qry.order_by(CancelledOrder.imported_at.desc()).all()

        # ส่งออกไปยังเทมเพลต
        return render_template(
            "dashboard_cancelled.html",
            rows=rows,
            q=q,
            platforms=platforms,
            shops=shops,
            platform_sel=platform_sel,
            shop_sel=shop_sel,
        )
    # =========[ /NEW ]=========

    # =========[ NEW ]=========  Dashboard: Order จ่ายแล้ว
    @app.route("/dashboard/issued")
    @login_required
    def dashboard_issued():
        if not current_user():
            return redirect(url_for("login"))

        q = (request.args.get("q") or "").strip()
        platform_sel = normalize_platform(request.args.get("platform"))
        shop_sel = request.args.get("shop_id")
        shop_sel = int(shop_sel) if shop_sel and str(shop_sel).isdigit() else None

        # สำหรับ dropdown เลือกแพลตฟอร์ม/ร้าน
        platforms = [p for (p,) in db.session.query(Shop.platform).filter(Shop.platform.isnot(None)).distinct().order_by(Shop.platform.asc()).all()]
        shop_query = Shop.query
        if platform_sel:
            shop_query = shop_query.filter(Shop.platform == platform_sel)
        shops = shop_query.order_by(Shop.name.asc()).all()

        # subquery map order_id -> (platform, shop_name, shop_id)
        sub = (
            db.session.query(
                OrderLine.order_id.label("oid"),
                func.min(OrderLine.shop_id).label("shop_id"),
                func.min(Shop.platform).label("platform"),
                func.min(Shop.name).label("shop_name"),
                func.min(OrderLine.logistic_type).label("logistic"),
            )
            .outerjoin(Shop, Shop.id == OrderLine.shop_id)
            .group_by(OrderLine.order_id)
            .subquery()
        )

        qry = (
            db.session.query(
                IssuedOrder.order_id,
                IssuedOrder.issued_at,
                sub.c.platform,
                sub.c.shop_name,
                sub.c.shop_id,
                sub.c.logistic,
            )
            .outerjoin(sub, sub.c.oid == IssuedOrder.order_id)
        )

        if q:
            qry = qry.filter(IssuedOrder.order_id.contains(q))
        if platform_sel:
            qry = qry.filter(sub.c.platform == platform_sel)
        if shop_sel:
            qry = qry.filter(sub.c.shop_id == shop_sel)

        rows = qry.order_by(IssuedOrder.issued_at.desc()).all()

        return render_template(
            "dashboard_issued.html",
            rows=rows, q=q, platforms=platforms, shops=shops,
            platform_sel=platform_sel, shop_sel=shop_sel
        )

    @app.post("/issued/unissue")
    @login_required
    def issued_unissue():
        cu = current_user()
        if not cu or cu.role not in {"admin", "staff"}:
            flash("ต้องเป็นผู้ดูแลระบบหรือพนักงานเท่านั้น", "danger")
            return redirect(url_for("dashboard_issued"))

        ids = request.form.getlist("order_ids[]")
        if not ids:
            oid = request.form.get("order_id")
            if oid:
                ids = [oid]
        n = _unissue(ids or [])
        if n > 0:
            flash(f"ยกเลิกจ่ายงานแล้ว {n} ออเดอร์", "success")
        else:
            flash("ไม่พบออเดอร์ที่จะยกเลิกจ่ายงาน", "warning")
        return redirect(url_for("dashboard_issued"))
    # =========[ /NEW ]=========

    @app.route("/import/products", methods=["GET", "POST"])
    @login_required
    def import_products_view():
        if request.method == "POST":
            f = request.files.get("file")
            if not f:
                flash("กรุณาเลือกไฟล์สินค้า", "danger")
                return redirect(url_for("import_products_view"))
            try:
                df = pd.read_excel(f)
                cnt = import_products(df)
                flash(f"นำเข้าสินค้าสำเร็จ {cnt} รายการ", "success")
                return redirect(url_for("dashboard"))
            except Exception as e:
                flash(f"เกิดข้อผิดพลาดในการนำเข้าสินค้า: {e}", "danger")
                return redirect(url_for("import_products_view"))
        return render_template("import_products.html")

    @app.route("/import/stock", methods=["GET", "POST"])
    @login_required
    def import_stock_view():
        if request.method == "POST":
            f = request.files.get("file")
            if not f:
                flash("กรุณาเลือกไฟล์สต็อก", "danger")
                return redirect(url_for("import_stock_view"))
            try:
                df = pd.read_excel(f)
                cnt = import_stock(df)
                flash(f"นำเข้าสต็อกสำเร็จ {cnt} รายการ", "success")
                return redirect(url_for("dashboard"))
            except Exception as e:
                flash(f"เกิดข้อผิดพลาดในการนำเข้าสต็อก: {e}", "danger")
                return redirect(url_for("import_stock_view"))
        return render_template("import_stock.html")

    @app.route("/import/sales", methods=["GET", "POST"])
    @login_required
    def import_sales_view():
        if request.method == "POST":
            f = request.files.get("file")
            if not f:
                flash("กรุณาเลือกไฟล์สั่งขาย", "danger")
                return redirect(url_for("import_sales_view"))
            try:
                df = pd.read_excel(f)
                cnt = import_sales(df)
                flash(f"นำเข้าไฟล์สั่งขายสำเร็จ {cnt} รายการ", "success")
                return redirect(url_for("dashboard"))
            except Exception as e:
                flash(f"เกิดข้อผิดพลาดในการนำเข้าไฟล์สั่งขาย: {e}", "danger")
                return redirect(url_for("import_sales_view"))
        return render_template("import_sales.html")

    # -----------------------
    # Accept / Cancel / Bulk
    # -----------------------
    @app.route("/accept/<int:order_line_id>", methods=["POST"])
    @login_required
    def accept_order(order_line_id):
        ol = OrderLine.query.get_or_404(order_line_id)
        # ห้ามกดรับถ้าเลข Order ถูกทำเป็นยกเลิก
        if db.session.query(CancelledOrder.id).filter_by(order_id=ol.order_id).first():
            flash(f"Order {ol.order_id} ถูกทำเป็น 'ยกเลิก' แล้ว — ไม่สามารถกดรับได้", "warning")
            return redirect(url_for("dashboard", **request.args))

        cu = current_user()
        sales_status = (getattr(ol, "sales_status", "") or "").upper()
        if sales_status == "PACKED" or bool(getattr(ol, "packed", False)):
            flash("รายการนี้ถูกแพ็คแล้ว (PACKED) — ไม่สามารถกดรับได้", "warning")
            return redirect(url_for("dashboard", **request.args))

        stock_qty = _calc_stock_qty_for_line(ol)
        if stock_qty <= 0:
            flash("สต็อกหมด — ไม่สามารถกดรับได้", "warning")
            return redirect(url_for("dashboard", **request.args))

        sku = _get_line_sku(ol)
        if not sku:
            flash("ไม่พบ SKU ของรายการนี้ — ไม่สามารถกดรับได้", "warning")
            return redirect(url_for("dashboard", **request.args))

        accepted_qty = db.session.query(func.coalesce(func.sum(OrderLine.qty), 0))\
            .filter(OrderLine.id != ol.id)\
            .filter(OrderLine.accepted.is_(True))\
            .filter(getattr(OrderLine, "sku") == sku).scalar() or 0

        proposed_total = int(accepted_qty) + int(ol.qty or 0)
        if proposed_total > int(stock_qty):
            flash("สินค้าไม่พอส่ง — ยอดที่รับจะเกินสต็อกรวมของ SKU นี้", "warning")
            return redirect(url_for("dashboard", **request.args))

        ol.accepted = True
        ol.accepted_at = now_thai()
        ol.accepted_by_user_id = cu.id if cu else None
        ol.accepted_by_username = cu.username if cu else None
        db.session.commit()
        flash(f"ทำเครื่องหมายกดรับ Order {ol.order_id} • SKU {sku} แล้ว", "success")
        return redirect(url_for("dashboard", **request.args))

    @app.route("/cancel_accept/<int:order_line_id>", methods=["POST"])
    @login_required
    def cancel_accept(order_line_id):
        ol = OrderLine.query.get_or_404(order_line_id)
        ol.accepted = False
        ol.accepted_at = None
        ol.accepted_by_user_id = None
        ol.accepted_by_username = None
        db.session.commit()
        flash(f"ยกเลิกการกดรับ Order {ol.order_id} • SKU {getattr(ol, 'sku', '')}", "warning")
        return redirect(url_for("dashboard", **request.args))

    @app.route("/bulk_accept", methods=["POST"])
    @login_required
    def bulk_accept():
        cu = current_user()
        order_line_ids = request.form.getlist("order_line_ids[]")
        if not order_line_ids:
            flash("กรุณาเลือกรายการที่ต้องการกดรับ", "warning")
            return redirect(url_for("dashboard", **request.args))
        success_count = 0
        error_messages = []
        for ol_id in order_line_ids:
            try:
                ol = OrderLine.query.get(int(ol_id))
                if not ol:
                    continue
                # block ถ้ายกเลิก
                if db.session.query(CancelledOrder.id).filter_by(order_id=ol.order_id).first():
                    error_messages.append(f"Order {ol.order_id} ถูกยกเลิก")
                    continue
                sales_status = (getattr(ol, "sales_status", "") or "").upper()
                if sales_status == "PACKED" or bool(getattr(ol, "packed", False)):
                    error_messages.append(f"Order {ol.order_id} ถูกแพ็คแล้ว")
                    continue
                stock_qty = _calc_stock_qty_for_line(ol)
                if stock_qty <= 0:
                    error_messages.append(f"Order {ol.order_id} สต็อกหมด")
                    continue
                sku = _get_line_sku(ol)
                if not sku:
                    error_messages.append(f"Order {ol.order_id} ไม่พบ SKU")
                    continue
                accepted_qty = db.session.query(func.coalesce(func.sum(OrderLine.qty), 0))\
                    .filter(OrderLine.id != ol.id)\
                    .filter(OrderLine.accepted.is_(True))\
                    .filter(getattr(OrderLine, "sku") == sku).scalar() or 0
                proposed_total = int(accepted_qty) + int(ol.qty or 0)
                if proposed_total > int(stock_qty):
                    error_messages.append(f"Order {ol.order_id} สินค้าไม่พอส่ง")
                    continue
                ol.accepted = True
                ol.accepted_at = now_thai()
                ol.accepted_by_user_id = cu.id if cu else None
                ol.accepted_by_username = cu.username if cu else None
                success_count += 1
            except Exception as e:
                error_messages.append(f"Order ID {ol_id}: {str(e)}")
                continue
        db.session.commit()
        if success_count > 0:
            flash(f"✅ กดรับสำเร็จ {success_count} รายการ", "success")
        if error_messages:
            for msg in error_messages[:5]:
                flash(f"⚠️ {msg}", "warning")
            if len(error_messages) > 5:
                flash(f"... และอีก {len(error_messages) - 5} รายการที่ไม่สามารถกดรับได้", "warning")
        return redirect(url_for("dashboard", **request.args))

    @app.route("/bulk_cancel", methods=["POST"])
    @login_required
    def bulk_cancel():
        order_line_ids = request.form.getlist("order_line_ids[]")
        if not order_line_ids:
            flash("กรุณาเลือกรายการที่ต้องการยกเลิก", "warning")
            return redirect(url_for("dashboard", **request.args))
        success_count = 0
        for ol_id in order_line_ids:
            try:
                ol = OrderLine.query.get(int(ol_id))
                if ol:
                    ol.accepted = False
                    ol.accepted_at = None
                    ol.accepted_by_user_id = None
                    ol.accepted_by_username = None
                    success_count += 1
            except Exception:
                continue
        db.session.commit()
        if success_count > 0:
            flash(f"✅ ยกเลิกสำเร็จ {success_count} รายการ", "success")
        return redirect(url_for("dashboard", **request.args))

    # ================== NEW: Bulk Delete Orders ==================
    @app.route("/bulk_delete_orders", methods=["POST"])
    @login_required
    def bulk_delete_orders():
        cu = current_user()
        if not cu or cu.role not in {"admin", "staff"}:
            flash("เฉพาะแอดมินหรือพนักงานเท่านั้นที่ลบได้", "danger")
            return redirect(url_for("dashboard", **request.args))

        ids = request.form.getlist("order_line_ids[]")
        if not ids:
            flash("กรุณาเลือกรายการที่ต้องการลบ", "warning")
            return redirect(url_for("dashboard", **request.args))

        # แปลง id -> set ของ order_id
        id_ints = [int(i) for i in ids if str(i).isdigit()]
        lines = OrderLine.query.filter(OrderLine.id.in_(id_ints)).all()
        oids = { (l.order_id or "").strip() for l in lines if l and l.order_id }
        if not oids:
            flash("ไม่พบเลข Order สำหรับลบ", "warning")
            return redirect(url_for("dashboard", **request.args))

        # ลบ OrderLine ทั้งออเดอร์
        deleted_lines = db.session.query(OrderLine).filter(OrderLine.order_id.in_(list(oids))).delete(synchronize_session=False)
        # ลบออกจาก cancelled ด้วย (กันค้าง)
        db.session.query(CancelledOrder).filter(CancelledOrder.order_id.in_(list(oids))).delete(synchronize_session=False)

        db.session.commit()
        flash(f"ลบ Order ออกจากระบบแล้ว {len(oids)} ออเดอร์ ({deleted_lines} แถว)", "success")
        return redirect(url_for("dashboard", **request.args))
    # ================== /NEW ==================

    # ================== NEW: Update Dispatch Round ==================
    @app.route("/update_dispatch_round", methods=["POST"])
    @login_required
    def update_dispatch_round():
        """Update dispatch_round for selected orders"""
        cu = current_user()
        if not cu:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        
        try:
            data = request.get_json()
            order_ids = data.get("order_ids", [])
            dispatch_round = data.get("dispatch_round")
            
            if not order_ids:
                return jsonify({"success": False, "error": "ไม่มีออเดอร์ที่เลือก"}), 400
            
            if dispatch_round is None or dispatch_round == "":
                return jsonify({"success": False, "error": "กรุณาระบุรอบการจ่ายงาน"}), 400
            
            # Convert to integer
            try:
                dispatch_round = int(dispatch_round)
            except (ValueError, TypeError):
                return jsonify({"success": False, "error": "รอบการจ่ายงานต้องเป็นตัวเลข"}), 400
            
            # Update all OrderLine records matching the order_ids
            updated = db.session.query(OrderLine).filter(
                OrderLine.order_id.in_(order_ids)
            ).update(
                {"dispatch_round": dispatch_round},
                synchronize_session=False
            )
            
            db.session.commit()
            
            return jsonify({
                "success": True,
                "message": f"อัปเดตรอบการจ่ายงานเป็น {dispatch_round} สำเร็จ {updated} รายการ",
                "updated": updated
            })
            
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "error": str(e)}), 500
    # ================== /NEW ==================

    # ================== NEW: Update Low Stock Round (ข้อ 1) ==================
    @app.route("/report/lowstock/update_round", methods=["POST"])
    @login_required
    def update_lowstock_round():
        """อัปเดต lowstock_round สำหรับออเดอร์ในรายงานสินค้าน้อย (ข้อ 1)"""
        cu = current_user()
        if not cu:
            return jsonify({"success": False, "message": "Unauthorized"}), 401

        data = request.get_json(silent=True) or {}
        order_ids = [str(s).strip() for s in (data.get("order_ids") or []) if str(s).strip()]
        round_raw = data.get("round")

        if not order_ids:
            return jsonify({"success": False, "message": "ไม่พบออเดอร์ในรายงานนี้"}), 400
        try:
            round_no = int(round_raw)
        except Exception:
            return jsonify({"success": False, "message": "รอบที่ต้องเป็นตัวเลข"}), 400

        # อัปเดตทุกบรรทัดของออเดอร์ที่เลือก (ใช้ raw SQL เพราะ lowstock_round ไม่มีในโมเดล)
        try:
            tbl = _ol_table_name()
            sql = text(f"""
                UPDATE {tbl}
                   SET lowstock_round = :r
                 WHERE order_id IN :oids
            """).bindparams(bindparam("oids", expanding=True))
            result = db.session.execute(sql, {"r": round_no, "oids": order_ids})
            db.session.commit()
            
            return jsonify({
                "success": True,
                "message": f"อัปเดตรอบเป็น {round_no} ให้ {result.rowcount} รายการ",
                "updated": result.rowcount
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": f"เกิดข้อผิดพลาด: {str(e)}"
            }), 500
    # ================== /NEW ==================

    # -----------------------
    # Export dashboard
    # -----------------------
    @app.route("/export.xlsx")
    @login_required
    def export_excel():
        platform = normalize_platform(request.args.get("platform"))
        shop_id = request.args.get("shop_id")
        import_date = request.args.get("import_date")
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")
        status = request.args.get("status")

        def _p(s):
            return parse_date_any(s)

        filters = {
            "platform": platform,
            "shop_id": int(shop_id) if shop_id else None,
            "import_date": _p(import_date),
            "date_from": datetime.combine(_p(date_from), datetime.min.time(), tzinfo=TH_TZ) if date_from else None,
            "date_to": datetime.combine(_p(date_to) + timedelta(days=1), datetime.min.time(), tzinfo=TH_TZ) if date_to else None,
        }

        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)
        rows = _filter_out_issued_rows(rows)  # <<< ตัดออเดอร์จ่ายแล้วออกให้ตรงกับ Dashboard

        for r in rows:
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try:
                            stock_qty = int(prod.stock_qty or 0)
                        except Exception:
                            stock_qty = 0
                    else:
                        st = Stock.query.filter_by(sku=sku).first()
                        stock_qty = int(st.qty) if st and st.qty is not None else 0
                r["stock_qty"] = stock_qty
            r["accepted"] = bool(r.get("accepted", False))
            r["sales_status"] = r.get("sales_status", None)
            r["logistic"] = r.get("logistic") or r.get("logistic_type") or "-"

        totals = _build_allqty_map(rows)
        for r in rows:
            r["allqty"] = int(totals.get((r.get("sku") or "").strip(), r.get("qty", 0)) or 0)
            _recompute_allocation_row(r)  # in-place

        packed_oids = _orders_packed_set(rows)
        for r in rows:
            if (r.get("order_id") or "").strip() in packed_oids:
                r["allocation_status"] = "PACKED"
                r["packed"] = True

        orders_ready = _orders_ready_set(rows)
        orders_low_order = _orders_lowstock_order_set(rows)

        status_norm = (status or "").strip().upper()
        if status_norm == "ORDER_READY":
            rows = [r for r in rows if (r.get("order_id") or "").strip() in orders_ready]
        elif status_norm in {"ORDER_LOW_STOCK", "ORDER_LOW"}:
            rows = [r for r in rows if (r.get("order_id") or "").strip() in orders_low_order]
        elif status_norm in {"PACKED"}:
            rows = [r for r in rows if (r.get("order_id") or "").strip() in packed_oids]
        elif status_norm:
            rows = [r for r in rows if (r.get("allocation_status") or "").strip().upper() == status_norm]
        else:
            rows = [r for r in rows if (r.get("order_id") or "").strip() not in packed_oids]

        rows = _annotate_order_spans(rows)

        df = pd.DataFrame([{
            "แพลตฟอร์ม": r.get("platform"),
            "ชื่อร้าน": r.get("shop"),
            "เลข Order": r.get("order_id_display"),
            "SKU": r.get("sku"),
            "Brand": r.get("brand"),
            "ชื่อสินค้า": r.get("model"),
            "Stock": r.get("stock_qty"),
            "Qty": r.get("qty"),
            "AllQty": r.get("allqty"),
            "เวลาที่ลูกค้าสั่ง": r.get("order_time"),
            "กำหนดส่ง": r.get("due_date"),
            "ประเภทขนส่ง": r.get("logistic"),
            "สั่งขาย": r.get("sales_status"),
            "สถานะ": r.get("allocation_status"),
            "ผู้กดรับ": r.get("accepted_by")
        } for r in rows])

        out = BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as w:
            df.to_excel(w, index=False, sheet_name="Dashboard")
        out.seek(0)
        return send_file(out, as_attachment=True, download_name="dashboard_export.xlsx")

    # -----------------------
    # ใบงานคลัง (Warehouse Job Sheet)
    # -----------------------
    @app.route("/report/warehouse", methods=["GET"])
    @login_required
    def print_warehouse():
        platform = normalize_platform(request.args.get("platform"))
        shop_id = request.args.get("shop_id")
        logistic = request.args.get("logistic")

        filters = {"platform": platform, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)
        rows = [r for r in rows if r.get("accepted") and r.get("allocation_status") in ("ACCEPTED", "READY_ACCEPT")]

        # *** กรองออเดอร์ที่พิมพ์แล้วออก - แสดงเฉพาะที่ยังไม่ได้พิมพ์ ***
        # ดึง count จาก DB จริงแทนที่จะใช้ r.get("printed_warehouse") ที่เป็น 0 ตลอด
        oids = sorted({(r.get("order_id") or "").strip() for r in rows if r.get("order_id")})
        counts = _get_print_counts_local(oids, kind="warehouse")
        rows = [r for r in rows if int(counts.get((r.get("order_id") or "").strip(), 0)) == 0]

        if logistic:
            rows = [r for r in rows if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]

        _inject_print_counts_to_rows(rows, kind="warehouse")
        rows = _group_rows_for_warehouse_report(rows)  # Use warehouse-specific grouping

        total_orders = len(rows)  # Now 1 row = 1 order
        shops = Shop.query.all()
        logistics = sorted(set(r.get("logistic") for r in rows if r.get("logistic")))
        return render_template(
            "report.html",
            rows=rows,
            count_orders=total_orders,
            shops=shops,
            logistics=logistics,
            platform_sel=platform,
            shop_sel=shop_id,
            logistic_sel=logistic,
            official_print=False,
            printed_meta=None
        )

    @app.route("/report/warehouse/print", methods=["POST"])
    @login_required
    def print_warehouse_commit():
        cu = current_user()
        platform = normalize_platform(request.form.get("platform"))
        shop_id = request.form.get("shop_id")
        logistic = request.form.get("logistic")
        override = request.form.get("override") in ("1", "true", "yes")
        
        # Get selected order IDs from form
        selected_order_ids = request.form.get("order_ids", "")
        selected_order_ids = [oid.strip() for oid in selected_order_ids.split(",") if oid.strip()]

        filters = {"platform": platform, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)
        rows = [r for r in rows if r.get("accepted") and r.get("allocation_status") in ("ACCEPTED", "READY_ACCEPT")]

        if logistic:
            rows = [r for r in rows if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]

        # If specific order IDs were selected, filter to only those orders
        if selected_order_ids:
            rows = [r for r in rows if (r.get("order_id") or "").strip() in selected_order_ids]
            oids = sorted(selected_order_ids)
        else:
            oids = sorted({(r.get("order_id") or "").strip() for r in rows if r.get("order_id")})
        
        if not oids:
            flash("ไม่พบออเดอร์สำหรับพิมพ์", "warning")
            return redirect(url_for("print_warehouse", platform=platform, shop_id=shop_id, logistic=logistic))

        already = _detect_already_printed(oids, kind="warehouse")
        if already and not (override and cu and cu.role == "admin"):
            head = ", ".join(list(already)[:10])
            more = "" if len(already) <= 10 else f" ... (+{len(already)-10})"
            flash(f"มีบางออเดอร์เคยพิมพ์ใบงานคลังไปแล้ว: {head}{more}", "danger")
            flash("ถ้าจำเป็นต้องพิมพ์ซ้ำ โปรดให้แอดมินกดยืนยัน 'อนุญาตพิมพ์ซ้ำ' แล้วพิมพ์อีกครั้ง", "warning")
            return redirect(url_for("print_warehouse", platform=platform, shop_id=shop_id, logistic=logistic))

        now_iso = now_thai().isoformat()
        _mark_printed(oids, kind="warehouse", user_id=(cu.id if cu else None), when_iso=now_iso)
        
        # >>> NEW: ย้ายไป Orderจ่ายแล้ว (บันทึกเวลาตอนพิมพ์)
        _mark_issued(oids, user_id=(cu.id if cu else None), source="print:warehouse", when_dt=now_thai())
        
        db.session.commit()  # Ensure changes are committed
        db.session.expire_all()  # Force refresh to get updated print counts

        _inject_print_counts_to_rows(rows, kind="warehouse")
        rows = _group_rows_for_warehouse_report(rows)  # Use warehouse-specific grouping

        total_orders = len(rows)  # Now 1 row = 1 order
        shops = Shop.query.all()
        logistics = sorted(set(r.get("logistic") for r in rows if r.get("logistic")))
        printed_meta = {"by": (cu.username if cu else "-"), "at": now_thai(), "orders": total_orders, "override": bool(already)}
        return render_template(
            "report.html",
            rows=rows,
            count_orders=total_orders,
            shops=shops,
            logistics=logistics,
            platform_sel=platform,
            shop_sel=shop_id,
            logistic_sel=logistic,
            official_print=True,
            printed_meta=printed_meta
        )

    # ================== NEW: View Printed Warehouse Jobs ==================
    @app.route("/report/warehouse/printed", methods=["GET"])
    @login_required
    def warehouse_printed_history():
        """ดูใบงานคลังที่พิมพ์แล้ว - สามารถเลือกวันที่และพิมพ์ซ้ำได้"""
        platform = normalize_platform(request.args.get("platform"))
        shop_id = request.args.get("shop_id")
        logistic = request.args.get("logistic")
        print_date = request.args.get("print_date")  # วันที่พิมพ์ (YYYY-MM-DD)
        
        # Get all orders that have been printed
        tbl = _ol_table_name()
        
        # Build query to get orders with print history
        if print_date:
            # Filter by specific print date
            try:
                target_date = datetime.strptime(print_date, "%Y-%m-%d").date()
                sql = text(f"""
                    SELECT DISTINCT order_id 
                    FROM {tbl} 
                    WHERE printed_warehouse > 0 
                    AND DATE(printed_warehouse_at) = :target_date
                """)
                result = db.session.execute(sql, {"target_date": target_date.isoformat()}).fetchall()
            except:
                result = []
        else:
            # Get all printed orders
            sql = text(f"SELECT DISTINCT order_id FROM {tbl} WHERE printed_warehouse > 0")
            result = db.session.execute(sql).fetchall()
        
        printed_order_ids = [row[0] for row in result if row[0]]
        
        if not printed_order_ids:
            # No printed orders found
            shops = Shop.query.all()
            return render_template(
                "report.html",
                rows=[],
                count_orders=0,
                shops=shops,
                logistics=[],
                platform_sel=platform,
                shop_sel=shop_id,
                logistic_sel=logistic,
                official_print=False,
                printed_meta=None,
                is_history_view=True,
                print_date_sel=print_date
            )
        
        # Get full data for these orders
        filters = {"platform": platform if platform else None, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)
        
        # Filter to only printed orders
        rows = [r for r in rows if (r.get("order_id") or "").strip() in printed_order_ids]
        
        if logistic:
            rows = [r for r in rows if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]
        
        _inject_print_counts_to_rows(rows, kind="warehouse")
        rows = _group_rows_for_warehouse_report(rows)
        
        total_orders = len(rows)
        shops = Shop.query.all()
        logistics = sorted(set(r.get("logistic") for r in rows if r.get("logistic")))
        
        # Get available print dates for dropdown
        sql_dates = text(f"""
            SELECT DISTINCT DATE(printed_warehouse_at) as print_date 
            FROM {tbl} 
            WHERE printed_warehouse > 0 AND printed_warehouse_at IS NOT NULL
            ORDER BY print_date DESC
        """)
        available_dates = [row[0] for row in db.session.execute(sql_dates).fetchall()]
        
        return render_template(
            "report.html",
            rows=rows,
            count_orders=total_orders,
            shops=shops,
            logistics=logistics,
            platform_sel=platform,
            shop_sel=shop_id,
            logistic_sel=logistic,
            official_print=False,
            printed_meta=None,
            is_history_view=True,
            print_date_sel=print_date,
            available_dates=available_dates
        )

    # ================== NEW: Low-Stock & No-Stock Reports ==================

    @app.route("/report/lowstock", methods=["GET"])
    @login_required
    def report_lowstock():
        """
        รายงานสินค้าน้อย — อ้างอิงชุด SKU/Order จาก Dashboard โดยตรง
        ข้อสำคัญตาม requirement:
          - ไม่ดึงออเดอร์ที่ PACKED แล้ว (ข้อ 1)
          - 'จ่ายงาน(รอบที่)' ใช้คอลัมน์ lowstock_round แยกจาก dispatch_round (ข้อ 2)
          - 'พิมพ์แล้ว(ครั้ง)' ใช้ printed_lowstock (ข้อ 3)
          - รองรับ filter ครบ (ข้อ 4)
          - รองรับ sort ทุกคอลัมน์ (ข้อ 5)
          - ดึงเฉพาะชุด Order สินค้าน้อยจาก Dashboard (ข้อ 6)
        """
        from services.lowstock_queue import get_lowstock_rows_from_allocation

        # ---- รับตัวกรอง/เรียง ----
        platform = normalize_platform(request.args.get("platform"))
        shop_id  = request.args.get("shop_id")
        logistic = request.args.get("logistic")
        round_num = request.args.get("round")  # ข้อ 7: กรองรอบ
        q        = (request.args.get("q") or "").strip()
        sort_col = (request.args.get("sort") or "").strip().lower()
        sort_dir = (request.args.get("dir") or "asc").lower()

        shops = Shop.query.order_by(Shop.name.asc()).all()

        # ---- 1) ดึง allocation rows เหมือน Dashboard ----
        filters = {
            "platform": platform if platform else None,
            "shop_id": int(shop_id) if shop_id else None,
            "import_date": None
        }
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)
        rows = _filter_out_issued_rows(rows)
        rows = _filter_out_lowstock_printed_rows(rows)  # <<<< NEW (ข้อ 2): ตัดออเดอร์ที่พิมพ์รายงานสินค้าน้อยออก

        # เติม stock_qty / logistic ให้ครบ + ไม่เอา PACKED (ข้อ 1)
        safe = []
        for r in rows:
            r = dict(r)
            sales_status = (str(r.get("sales_status") or "")).upper()
            if sales_status == "PACKED" or bool(r.get("packed", False)):
                continue
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try:
                            stock_qty = int(prod.stock_qty or 0)
                        except Exception:
                            stock_qty = 0
                    else:
                        st = Stock.query.filter_by(sku=sku).first()
                        stock_qty = int(st.qty) if st and st.qty is not None else 0
                r["stock_qty"] = stock_qty
            r["logistic"] = r.get("logistic") or r.get("logistic_type") or "-"
            _recompute_allocation_row(r)
            safe.append(r)

        # ---- 2) ให้ "Order สินค้าน้อย" เป็นตัวตั้ง (ข้อ 6) ----
        orders_low = _orders_lowstock_order_set(safe)
        safe = [r for r in safe if (r.get("order_id") or "").strip() in orders_low]

        # ---- 3) เอาเฉพาะ SKU ที่เป็น "สินค้าน้อย" ----
        low_items = get_lowstock_rows_from_allocation(safe)
        low_skus  = {(it.get("sku") or "").strip() for it in low_items if it.get("sku")}
        lines = [r for r in safe if (r.get("sku") or "").strip() in low_skus]

        # ---- 4) กรองเพิ่มตามคำค้น/โลจิสติกส์ (ข้อ 4) ----
        if logistic:
            lines = [r for r in lines if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]
        if q:
            ql = q.lower()
            def _hit(s):
                return ql in (str(s or "").lower())
            lines = [r for r in lines if (
                _hit(r.get("order_id")) or _hit(r.get("sku")) or _hit(r.get("brand")) or
                _hit(r.get("model")) or _hit(r.get("shop")) or _hit(r.get("platform")) or _hit(r.get("logistic"))
            )]

        # ---- NEW (ข้อ 1): อ่านค่า lowstock_round จาก DB เผื่อ compute_allocation ไม่ส่งฟิลด์มา ----
        order_ids_for_round = sorted({(r.get("order_id") or "").strip() for r in lines if r.get("order_id")})
        low_round_by_oid = {}
        if order_ids_for_round:
            # ใช้ raw SQL แทน ORM เพราะ lowstock_round ไม่มีในโมเดล
            tbl = _ol_table_name()
            sql = text(f"""
                SELECT order_id, MAX(lowstock_round) AS r
                  FROM {tbl}
                 WHERE order_id IN :oids
                 GROUP BY order_id
            """).bindparams(bindparam("oids", expanding=True))
            try:
                q_round = db.session.execute(sql, {"oids": order_ids_for_round}).all()
                low_round_by_oid = {str(r[0]): (int(r[1]) if r[1] is not None else None) for r in q_round}
            except Exception:
                # ถ้าคอลัมน์ยังไม่มี ให้ใช้ค่าว่าง
                low_round_by_oid = {}

        # ---- 5) แปลงเป็นคอลัมน์ของรายงาน + AllQty ----
        out = []
        for r in lines:
            oid = (r.get("order_id") or "").strip()
            out.append({
                "platform":      r.get("platform"),
                "store":         r.get("shop"),
                "order_no":      oid,
                "sku":           r.get("sku"),
                "brand":         r.get("brand"),
                "product_name":  r.get("model"),
                "stock":         int(r.get("stock_qty", 0) or 0),
                "qty":           int(r.get("qty", 0) or 0),
                "order_time":    r.get("order_time"),
                "due_date":      r.get("due_date"),
                "sla":           r.get("sla"),
                "shipping_type": r.get("logistic"),
                "assign_round":  low_round_by_oid.get(oid, r.get("lowstock_round")),  # <<<< ใช้ค่าจาก DB (ข้อ 1)
                "printed_count": 0,
            })
        from collections import defaultdict
        sum_by_sku = defaultdict(int)
        for r in out:
            sum_by_sku[(r["sku"] or "").strip()] += int(r["qty"] or 0)
        for r in out:
            r["allqty"] = sum_by_sku[(r["sku"] or "").strip()]

        # ---- 6) เรียงลำดับ (ข้อ 5) ----
        sort_col = sort_col if sort_col in {"platform","store","order_no","sku","brand","product_name","stock","qty","allqty","order_time","due_date","sla","shipping_type","assign_round","printed_count"} else "order_no"
        rev = (sort_dir == "desc")
        def _key(v):
            if sort_col in {"stock","qty","allqty","assign_round","printed_count"}:
                try: return int(v.get(sort_col) or 0)
                except: return 0
            elif sort_col in {"order_time","due_date"}:
                try: return datetime.fromisoformat(str(v.get(sort_col)))
                except: return str(v.get(sort_col) or "")
            else:
                return str(v.get(sort_col) or "")
        out.sort(key=_key, reverse=rev)

        # ---- 7) นับ "พิมพ์แล้ว(ครั้ง)" (ข้อ 3) ----
        order_ids = sorted({(r["order_no"] or "").strip() for r in out if r.get("order_no")})
        counts_low = _get_print_counts_local(order_ids, "lowstock")
        for r in out:
            oid = (r.get("order_no") or "").strip()
            r["printed_count"] = int(counts_low.get(oid, 0))

        # ---- 8) เตรียม context สำหรับ template ----
        summary = {"sku_count": len(low_skus), "orders_count": len(order_ids)}
        # ข้อ 1: ไม่ต้องแสดงเวลาพิมพ์ในหน้าปกติ (ยังไม่ได้พิมพ์จริง)
        for r in out:
            r["printed_at"] = None  # ไม่ใส่เวลา

        logistics = sorted(set([r.get("shipping_type") for r in out if r.get("shipping_type")]))
        
        # ข้อ 7: หา available rounds สำหรับ dropdown
        available_rounds = sorted({r["assign_round"] for r in out if r["assign_round"] is not None})
        if not available_rounds:
            rs = db.session.execute(text("SELECT DISTINCT lowstock_round FROM order_lines WHERE lowstock_round IS NOT NULL ORDER BY lowstock_round")).fetchall()
            available_rounds = [x[0] for x in rs]

        return render_template(
            "report_lowstock.html",
            rows=out,
            summary=summary,
            printed_at=None,  # ข้อ 1: ไม่แสดงเวลาพิมพ์ในหน้าปกติ
            order_ids=order_ids,
            shops=shops,
            logistics=logistics,
            platform_sel=platform,
            shop_sel=shop_id,
            logistic_sel=logistic,
            round_sel=round_num,
            available_rounds=available_rounds,
            sort_col=sort_col,
            sort_dir=("desc" if rev else "asc"),
            q=q,
            is_history_view=False
        )

    @app.post("/report/lowstock/print")
    @login_required
    def report_lowstock_print():
        """บันทึกการพิมพ์รายงานสินค้าน้อย + ย้ายไปหน้าประวัติ (ข้อ 7)"""
        cu = current_user()
        order_ids_raw = (request.form.get("order_ids") or "").strip()
        order_ids = [s.strip() for s in order_ids_raw.split(",") if s.strip()]
        if not order_ids:
            flash("ไม่พบออเดอร์สำหรับพิมพ์", "warning")
            return redirect(url_for("report_lowstock"))

        now_iso = now_thai().isoformat()
        _mark_lowstock_printed(order_ids, username=(cu.username if cu else None), when_iso=now_iso)
        db.session.commit()
        return redirect(url_for("report_lowstock_printed"))

    @app.get("/report/lowstock/printed")
    @login_required
    def report_lowstock_printed():
        """ประวัติรายงานสินค้าน้อยที่พิมพ์แล้ว (ข้อ 7)"""
        from services.lowstock_queue import get_lowstock_rows_from_allocation
        
        platform = normalize_platform(request.args.get("platform"))
        shop_id  = request.args.get("shop_id")
        logistic = request.args.get("logistic")
        print_date = request.args.get("print_date")
        round_num = request.args.get("round")
        sort_col = (request.args.get("sort") or "order_no").strip().lower()
        sort_dir = (request.args.get("dir") or "asc").lower()

        tbl = _ol_table_name()
        if print_date:
            sql = text(f"SELECT DISTINCT order_id FROM {tbl} WHERE printed_lowstock > 0 AND DATE(printed_lowstock_at) = :d")
            result = db.session.execute(sql, {"d": print_date}).fetchall()
        else:
            result = db.session.execute(text(f"SELECT DISTINCT order_id FROM {tbl} WHERE printed_lowstock > 0")).fetchall()
        printed_oids = [r[0] for r in result if r and r[0]]

        def _available_dates():
            sql = text(f"SELECT DISTINCT DATE(printed_lowstock_at) as d FROM {tbl} WHERE printed_lowstock > 0 AND printed_lowstock_at IS NOT NULL ORDER BY d DESC")
            return [r[0] for r in db.session.execute(sql).fetchall()]

        shops = Shop.query.order_by(Shop.name.asc()).all()
        
        if not printed_oids:
            return render_template(
                "report_lowstock.html",
                rows=[],
                summary={"sku_count": 0, "orders_count": 0},
                printed_at=now_thai(),
                order_ids=[],
                shops=shops,
                logistics=[],
                platform_sel=platform,
                shop_sel=shop_id,
                logistic_sel=logistic,
                is_history_view=True,
                available_dates=_available_dates(),
                print_date_sel=print_date,
                sort_col=sort_col,
                sort_dir=sort_dir,
                q="",
                round_sel=round_num
            )

        filters = {"platform": platform if platform else None, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)
        rows = [r for r in rows if (r.get("order_id") or "").strip() in printed_oids]

        safe = []
        for r in rows:
            r = dict(r)
            r["logistic"] = r.get("logistic") or r.get("logistic_type") or "-"
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try: stock_qty = int(prod.stock_qty or 0)
                        except Exception: stock_qty = 0
                    else:
                        st = Stock.query.filter_by(sku=sku).first()
                        stock_qty = int(st.qty) if st and st.qty is not None else 0
                r["stock_qty"] = stock_qty
            _recompute_allocation_row(r)
            safe.append(r)

        low_items = get_lowstock_rows_from_allocation(safe)
        low_skus  = {(it.get("sku") or "").strip() for it in low_items if it.get("sku")}
        lines = [r for r in safe if (r.get("sku") or "").strip() in low_skus]

        if logistic:
            lines = [r for r in lines if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]

        out = []
        for r in lines:
            out.append({
                "platform":      r.get("platform"),
                "store":         r.get("shop"),
                "order_no":      r.get("order_id"),
                "sku":           r.get("sku"),
                "brand":         r.get("brand"),
                "product_name":  r.get("model"),
                "stock":         int(r.get("stock_qty", 0) or 0),
                "qty":           int(r.get("qty", 0) or 0),
                "order_time":    r.get("order_time"),
                "due_date":      r.get("due_date"),
                "sla":           r.get("sla"),
                "shipping_type": r.get("logistic"),
                "assign_round":  r.get("lowstock_round"),
                "printed_count": 0,
            })
        from collections import defaultdict
        sum_by_sku = defaultdict(int)
        for r in out:
            sum_by_sku[(r["sku"] or "").strip()] += int(r["qty"] or 0)
        for r in out:
            r["allqty"] = sum_by_sku[(r["sku"] or "").strip()]

        # เรียง
        sort_col = sort_col if sort_col in {"platform","store","order_no","sku","brand","product_name","stock","qty","allqty","order_time","due_date","sla","shipping_type","assign_round","printed_count"} else "order_no"
        rev = (sort_dir == "desc")
        def _key(v):
            if sort_col in {"stock","qty","allqty","assign_round","printed_count"}:
                try: return int(v.get(sort_col) or 0)
                except: return 0
            elif sort_col in {"order_time","due_date"}:
                try: return datetime.fromisoformat(str(v.get(sort_col)))
                except: return str(v.get(sort_col) or "")
            else:
                return str(v.get(sort_col) or "")
        out.sort(key=_key, reverse=rev)

        order_ids = sorted({(r["order_no"] or "").strip() for r in out if r.get("order_no")})
        counts_low = _get_print_counts_local(order_ids, "lowstock")
        for r in out:
            oid = (r.get("order_no") or "").strip()
            r["printed_count"] = int(counts_low.get(oid, 0))

        # ข้อ 1: ดึงเวลา printed_lowstock_at ต่อ order_id จาก DB
        tbl = _ol_table_name()
        sql_ts = text(f"""
            SELECT order_id, MAX(printed_lowstock_at) AS ts
            FROM {tbl}
            WHERE order_id IN :oids AND printed_lowstock_at IS NOT NULL
            GROUP BY order_id
        """).bindparams(bindparam("oids", expanding=True))
        rows_ts = db.session.execute(sql_ts, {"oids": order_ids}).all()
        ts_map = {}
        for oid, ts in rows_ts:
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = TH_TZ.localize(dt)
                ts_map[str(oid)] = dt
            except Exception:
                pass

        # ใส่ลงในแต่ละแถว
        for r in out:
            r["printed_at"] = ts_map.get((r.get("order_no") or "").strip())

        # เวลาพิมพ์บนหัวรายงาน (ล่าสุดสุดในชุด)
        meta_printed_at = max(ts_map.values()) if ts_map else None

        # ดึงค่า lowstock_round จาก DB เพื่อให้แน่ใจว่าหน้าประวัติแสดงเลขรอบ (แก้ปัญหาเลขหาย)
        if order_ids:
            tbl = _ol_table_name()
            sql = text(f"""
                SELECT order_id, MAX(lowstock_round) AS r
                  FROM {tbl}
                 WHERE order_id IN :oids
                 GROUP BY order_id
            """).bindparams(bindparam("oids", expanding=True))
            try:
                q_round = db.session.execute(sql, {"oids": order_ids}).all()
                round_map = {str(r[0]): (int(r[1]) if r[1] is not None else None) for r in q_round}
                for r in out:
                    oid = (r.get("order_no") or "").strip()
                    if oid in round_map and round_map[oid] is not None:
                        r["assign_round"] = round_map[oid]
            except Exception:
                pass  # ถ้าคอลัมน์ยังไม่มีก็ข้าม

        # กรองตามรอบ (หลังจากดึงค่าจาก DB แล้ว)
        if round_num and round_num != "all":
            try:
                r_int = int(round_num)
                out = [r for r in out if r.get("assign_round") == r_int]
                # อัปเดต order_ids หลังกรอง
                order_ids = sorted({(r["order_no"] or "").strip() for r in out if r.get("order_no")})
            except:
                pass

        logistics = sorted(set([r.get("shipping_type") for r in out if r.get("shipping_type")]))

        return render_template(
            "report_lowstock.html",
            rows=out,
            summary={"sku_count": len(low_skus), "orders_count": len(order_ids)},
            printed_at=meta_printed_at,  # ข้อ 1: ใช้เวลาจริงที่ถูกบันทึกไว้
            order_ids=order_ids,
            shops=shops,
            logistics=logistics,
            platform_sel=platform,
            shop_sel=shop_id,
            logistic_sel=logistic,
            is_history_view=True,
            available_dates=_available_dates(),
            print_date_sel=print_date,
            sort_col=sort_col,
            sort_dir=sort_dir,
            q="",
            round_sel=round_num
        )

    @app.route("/report/lowstock.xlsx", methods=["GET"])
    @login_required
    def report_lowstock_export():
        """ส่งออกรายงานสินค้าน้อยเป็น Excel (ข้อ 2: ตรงกับตารางในหน้าเว็บ)"""
        from services.lowstock_queue import get_lowstock_rows_from_allocation
        
        platform = normalize_platform(request.args.get("platform"))
        shop_id  = request.args.get("shop_id")
        logistic = request.args.get("logistic")
        q        = (request.args.get("q") or "").strip()
        sort_col = (request.args.get("sort") or "order_no").strip().lower()
        sort_dir = (request.args.get("dir") or "asc").lower()
        round_num = request.args.get("round")

        filters = {
            "platform": platform if platform else None,
            "shop_id": int(shop_id) if shop_id else None,
            "import_date": None
        }
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)
        rows = _filter_out_issued_rows(rows)
        
        # ข้อ 4: กรอง PACKED
        safe = []
        for r in rows:
            r = dict(r)
            sales_status = (str(r.get("sales_status") or "")).upper()
            if sales_status == "PACKED" or bool(r.get("packed", False)):
                continue
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try: stock_qty = int(prod.stock_qty or 0)
                        except: stock_qty = 0
                    else:
                        st = Stock.query.filter_by(sku=sku).first()
                        stock_qty = int(st.qty) if st and st.qty is not None else 0
                r["stock_qty"] = stock_qty
            r["logistic"] = r.get("logistic") or r.get("logistic_type") or "-"
            _recompute_allocation_row(r)
            safe.append(r)

        orders_low = _orders_lowstock_order_set(safe)
        safe = [r for r in safe if (r.get("order_id") or "").strip() in orders_low]
        
        low_items = get_lowstock_rows_from_allocation(safe)
        low_skus  = {(it.get("sku") or "").strip() for it in low_items if it.get("sku")}
        lines = [r for r in safe if (r.get("sku") or "").strip() in low_skus]

        # กรองเพิ่ม
        if logistic:
            lines = [r for r in lines if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]
        if q:
            ql = q.lower()
            def _hit(s): return ql in (str(s or "").lower())
            lines = [r for r in lines if (
                _hit(r.get("order_id")) or _hit(r.get("sku")) or _hit(r.get("brand")) or
                _hit(r.get("model")) or _hit(r.get("shop")) or _hit(r.get("platform")) or _hit(r.get("logistic"))
            )]
        if round_num and round_num != "all":
            try:
                r_int = int(round_num)
                lines = [r for r in lines if r.get("lowstock_round") == r_int]
            except: pass

        # คำนวณ AllQty
        from collections import defaultdict
        sum_by_sku = defaultdict(int)
        for r in lines:
            sum_by_sku[(r.get("sku") or "").strip()] += int(r.get("qty") or 0)

        # อ่านค่า lowstock_round จาก DB เหมือนหน้ารายงาน (ข้อ 1)
        order_ids_for_round = sorted({(r.get("order_id") or "").strip() for r in lines if r.get("order_id")})
        low_round_by_oid = {}
        if order_ids_for_round:
            # ใช้ raw SQL แทน ORM เพราะ lowstock_round ไม่มีในโมเดล
            tbl = _ol_table_name()
            sql = text(f"""
                SELECT order_id, MAX(lowstock_round) AS r
                  FROM {tbl}
                 WHERE order_id IN :oids
                 GROUP BY order_id
            """).bindparams(bindparam("oids", expanding=True))
            try:
                q_round = db.session.execute(sql, {"oids": order_ids_for_round}).all()
                low_round_by_oid = {str(r[0]): (int(r[1]) if r[1] is not None else None) for r in q_round}
            except Exception:
                low_round_by_oid = {}

        # สร้าง output rows
        out = []
        for r in lines:
            sku = (r.get("sku") or "").strip()
            oid = (r.get("order_id") or "").strip()
            out.append({
                "platform":      r.get("platform"),
                "store":         r.get("shop"),
                "order_no":      oid,
                "sku":           sku,
                "brand":         r.get("brand"),
                "product_name":  r.get("model"),
                "stock":         int(r.get("stock_qty", 0) or 0),
                "qty":           int(r.get("qty", 0) or 0),
                "allqty":        sum_by_sku[sku],
                "order_time":    r.get("order_time"),
                "due_date":      r.get("due_date"),
                "sla":           r.get("sla"),
                "shipping_type": r.get("logistic"),
                "assign_round":  low_round_by_oid.get(oid, r.get("lowstock_round")),  # <<<< ใช้ค่าจาก DB
            })

        # เรียง
        sort_col = sort_col if sort_col in {"platform","store","order_no","sku","brand","product_name","stock","qty","allqty","order_time","due_date","sla","shipping_type","assign_round","printed_count"} else "order_no"
        rev = (sort_dir == "desc")
        def _key(v):
            if sort_col in {"stock","qty","allqty","assign_round","printed_count"}:
                try: return int(v.get(sort_col) or 0)
                except: return 0
            elif sort_col in {"order_time","due_date"}:
                try: return datetime.fromisoformat(str(v.get(sort_col)))
                except: return str(v.get(sort_col) or "")
            else:
                return str(v.get(sort_col) or "")
        out.sort(key=_key, reverse=rev)

        # เพิ่มคอลัมน์ "พิมพ์แล้ว(ครั้ง)"
        order_ids = sorted({(r["order_no"] or "").strip() for r in out if r.get("order_no")})
        counts_low = _get_print_counts_local(order_ids, "lowstock")
        for r in out:
            oid = (r.get("order_no") or "").strip()
            r["printed_count"] = int(counts_low.get(oid, 0))
        
        # สร้าง DataFrame
        df_data = []
        for r in out:
            df_data.append({
                "แพลตฟอร์ม": r["platform"],
                "ร้าน": r["store"],
                "เลข Order": r["order_no"],
                "SKU": r["sku"],
                "Brand": r["brand"],
                "ชื่อสินค้า": r["product_name"],
                "Stock": r["stock"],
                "Qty": r["qty"],
                "AllQty": r["allqty"],
                "เวลาที่ลูกค้าสั่ง": r["order_time"],
                "กำหนดส่ง": r["due_date"],
                "SLA (ชม.)": r["sla"],
                "ประเภทขนส่ง": r["shipping_type"],
                "จ่ายงาน(รอบที่)": r["assign_round"] if r["assign_round"] is not None else "",
                "พิมพ์แล้ว(ครั้ง)": r["printed_count"],
            })

        df = pd.DataFrame(df_data)
        bio = BytesIO()
        with pd.ExcelWriter(bio, engine="xlsxwriter") as w:
            df.to_excel(w, index=False, sheet_name="LowStock")
        bio.seek(0)
        
        filename = f"lowstock_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(
            bio,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


    @app.route("/report/nostock", methods=["GET"])
    @login_required
    def report_nostock():
        """
        รายงานไม่มีสินค้า — กรองเฉพาะ SHORTAGE (stock = 0) เท่านั้น
        """
        platform = normalize_platform(request.args.get("platform"))
        shop_id  = request.args.get("shop_id")
        logistic = request.args.get("logistic")
        round_num = request.args.get("round")
        q        = (request.args.get("q") or "").strip()
        sort_col = (request.args.get("sort") or "").strip().lower()
        sort_dir = (request.args.get("dir") or "asc").lower()

        shops = Shop.query.order_by(Shop.name.asc()).all()

        # 1) ดึง allocation rows
        filters = {"platform": platform or None, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)
        rows = _filter_out_issued_rows(rows)

        # เติม stock_qty/logistic
        safe = []
        for r in rows:
            r = dict(r)
            if (str(r.get("sales_status") or "")).upper() == "PACKED" or bool(r.get("packed", False)):
                continue
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try:
                            stock_qty = int(prod.stock_qty or 0)
                        except Exception:
                            stock_qty = 0
                    else:
                        st = Stock.query.filter_by(sku=sku).first()
                        stock_qty = int(st.qty) if st and st.qty is not None else 0
                r["stock_qty"] = stock_qty
            r["logistic"] = r.get("logistic") or r.get("logistic_type") or "-"
            _recompute_allocation_row(r)
            safe.append(r)

        # 2) กรองเฉพาะ SHORTAGE (stock = 0) เท่านั้น
        def is_nostock(r):
            try:
                stk = int(r.get("stock_qty") or 0)
            except:
                stk = 0
            return (r.get("allocation_status") == "SHORTAGE") or (stk <= 0)
        
        lines = [r for r in safe if is_nostock(r)]

        # 3) ฟิลเตอร์
        if logistic:
            lines = [r for r in lines if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]
        if q:
            ql = q.lower()
            lines = [r for r in lines if ql in (str(r.get("order_id","")) + str(r.get("sku","")) + 
                    str(r.get("brand","")) + str(r.get("model","")) + str(r.get("shop",""))).lower()]

        # 4) ดึงค่า nostock_round จาก DB
        order_ids_for_round = sorted({(r.get("order_id") or "").strip() for r in lines if r.get("order_id")})
        nostock_round_by_oid = {}
        if order_ids_for_round:
            tbl = _ol_table_name()
            sql = text(f"SELECT order_id, MAX(nostock_round) AS r FROM {tbl} WHERE order_id IN :oids GROUP BY order_id")
            sql = sql.bindparams(bindparam("oids", expanding=True))
            try:
                q_round = db.session.execute(sql, {"oids": order_ids_for_round}).all()
                nostock_round_by_oid = {str(r[0]): (int(r[1]) if r[1] is not None else None) for r in q_round}
            except Exception:
                nostock_round_by_oid = {}

        # กรองตาม round ถ้ามีเลือก
        if round_num not in (None, "", "all"):
            try:
                round_filter = int(round_num)
                lines = [r for r in lines if nostock_round_by_oid.get((r.get("order_id") or "").strip()) == round_filter]
            except:
                pass

        # 5) แปลงเป็นคอลัมน์รายงาน
        out = []
        for r in lines:
            oid = (r.get("order_id") or "").strip()
            out.append({
                "platform":      r.get("platform"),
                "store":         r.get("shop"),
                "order_no":      oid,
                "sku":           r.get("sku"),
                "brand":         r.get("brand"),
                "product_name":  r.get("model"),
                "stock":         int(r.get("stock_qty", 0) or 0),
                "qty":           int(r.get("qty", 0) or 0),
                "order_time":    r.get("order_time"),
                "due_date":      r.get("due_date"),
                "sla":           r.get("sla"),
                "shipping_type": r.get("logistic"),
                "assign_round":  nostock_round_by_oid.get(oid, r.get("nostock_round")),
                "printed_count": 0,
            })
        
        from collections import defaultdict
        sum_by_sku = defaultdict(int)
        for r in out:
            sum_by_sku[(r["sku"] or "").strip()] += int(r["qty"] or 0)
        for r in out:
            r["allqty"] = sum_by_sku[(r["sku"] or "").strip()]

        # 6) เรียงลำดับ
        sort_col = sort_col if sort_col in {"platform","store","order_no","sku","brand","product_name","stock","qty","allqty","order_time","due_date","sla","shipping_type","assign_round","printed_count"} else "order_no"
        rev = (sort_dir == "desc")
        def _key(v):
            if sort_col in {"stock","qty","allqty","assign_round","printed_count"}:
                try: return int(v.get(sort_col) or 0)
                except: return 0
            elif sort_col in {"order_time","due_date"}:
                try: return datetime.fromisoformat(str(v.get(sort_col)))
                except: return str(v.get(sort_col) or "")
            else:
                return str(v.get(sort_col) or "")
        out.sort(key=_key, reverse=rev)

        # 7) นับ "พิมพ์แล้ว(ครั้ง)"
        order_ids = sorted({(r["order_no"] or "").strip() for r in out if r.get("order_no")})
        counts_nostock = _get_print_counts_local(order_ids, "nostock")
        for r in out:
            oid = (r.get("order_no") or "").strip()
            r["printed_count"] = int(counts_nostock.get(oid, 0))
            r["printed_at"] = None  # ไม่แสดงเวลาในหน้าปกติ

        # 8) กรองเฉพาะออเดอร์ที่ยังไม่พิมพ์
        out = [r for r in out if (r.get("printed_count") or 0) == 0]

        # 9) คำนวณสรุป + order_ids ใหม่หลังกรอง
        order_ids = sorted({(r.get("order_no") or "").strip() for r in out if r.get("order_no")})
        nostock_skus = {(r["sku"] or "").strip() for r in out if r.get("sku")}
        summary = {"sku_count": len(nostock_skus), "orders_count": len(order_ids)}

        logistics = sorted(set([r.get("shipping_type") for r in out if r.get("shipping_type")]))
        available_rounds = sorted({r["assign_round"] for r in out if r["assign_round"] is not None})

        return render_template(
            "report_nostock.html",
            rows=out,
            summary=summary,
            printed_at=None,
            order_ids=order_ids,
            shops=shops,
            logistics=logistics,
            platform_sel=platform,
            shop_sel=shop_id,
            logistic_sel=logistic,
            round_sel=round_num,
            available_rounds=available_rounds,
            sort_col=sort_col,
            sort_dir=("desc" if rev else "asc"),
            q=q,
            is_history_view=False
        )

    @app.post("/report/nostock/print")
    @login_required
    def report_nostock_print():
        """บันทึกการพิมพ์รายงานไม่มีสินค้า + ย้ายไปหน้าประวัติ"""
        cu = current_user()
        order_ids_raw = (request.form.get("order_ids") or "").strip()
        order_ids = [s.strip() for s in order_ids_raw.split(",") if s.strip()]
        if not order_ids:
            flash("ไม่พบออเดอร์สำหรับพิมพ์", "warning")
            return redirect(url_for("report_nostock"))

        now_iso = now_thai().isoformat()
        _mark_nostock_printed(order_ids, username=(cu.username if cu else None), when_iso=now_iso)
        db.session.commit()
        return redirect(url_for("report_nostock_printed"))

    @app.get("/report/nostock/printed")
    @login_required
    def report_nostock_printed():
        """ประวัติรายงานไม่มีสินค้าที่พิมพ์แล้ว"""
        platform = normalize_platform(request.args.get("platform"))
        shop_id  = request.args.get("shop_id")
        logistic = request.args.get("logistic")
        print_date = request.args.get("print_date")
        round_num = request.args.get("round")
        sort_col = (request.args.get("sort") or "order_no").strip().lower()
        sort_dir = (request.args.get("dir") or "asc").lower()

        tbl = _ol_table_name()
        if print_date:
            sql = text(f"SELECT DISTINCT order_id FROM {tbl} WHERE printed_nostock > 0 AND DATE(printed_nostock_at) = :d")
            result = db.session.execute(sql, {"d": print_date}).fetchall()
        else:
            result = db.session.execute(text(f"SELECT DISTINCT order_id FROM {tbl} WHERE printed_nostock > 0")).fetchall()
        printed_oids = [r[0] for r in result if r and r[0]]

        def _available_dates():
            sql = text(f"SELECT DISTINCT DATE(printed_nostock_at) as d FROM {tbl} WHERE printed_nostock > 0 AND printed_nostock_at IS NOT NULL ORDER BY d DESC")
            return [r[0] for r in db.session.execute(sql).fetchall()]

        shops = Shop.query.order_by(Shop.name.asc()).all()
        
        if not printed_oids:
            return render_template(
                "report_nostock.html",
                rows=[],
                summary={"sku_count": 0, "orders_count": 0},
                printed_at=None,
                order_ids=[],
                shops=shops,
                logistics=[],
                platform_sel=platform,
                shop_sel=shop_id,
                logistic_sel=logistic,
                is_history_view=True,
                available_dates=_available_dates(),
                print_date_sel=print_date,
                sort_col=sort_col,
                sort_dir=sort_dir,
                q="",
                round_sel=round_num
            )

        filters = {"platform": platform if platform else None, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)
        rows = [r for r in rows if (r.get("order_id") or "").strip() in printed_oids]

        safe = []
        for r in rows:
            r = dict(r)
            r["logistic"] = r.get("logistic") or r.get("logistic_type") or "-"
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try: stock_qty = int(prod.stock_qty or 0)
                        except Exception: stock_qty = 0
                    else:
                        st = Stock.query.filter_by(sku=sku).first()
                        stock_qty = int(st.qty) if st and st.qty is not None else 0
                r["stock_qty"] = stock_qty
            _recompute_allocation_row(r)
            safe.append(r)

        # กรองเฉพาะ SHORTAGE (stock = 0)
        def is_nostock(r):
            try:
                stk = int(r.get("stock_qty") or 0)
            except:
                stk = 0
            return (r.get("allocation_status") == "SHORTAGE") or (stk <= 0)
        
        lines = [r for r in safe if is_nostock(r)]

        if logistic:
            lines = [r for r in lines if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]

        # ดึงค่า nostock_round จาก DB
        order_ids_for_round = sorted({(r.get("order_id") or "").strip() for r in lines if r.get("order_id")})
        nostock_round_by_oid = {}
        if order_ids_for_round:
            sql = text(f"SELECT order_id, MAX(nostock_round) AS r FROM {tbl} WHERE order_id IN :oids GROUP BY order_id")
            sql = sql.bindparams(bindparam("oids", expanding=True))
            try:
                q_round = db.session.execute(sql, {"oids": order_ids_for_round}).all()
                nostock_round_by_oid = {str(r[0]): (int(r[1]) if r[1] is not None else None) for r in q_round}
            except Exception:
                nostock_round_by_oid = {}

        # กรองตาม round ถ้ามี
        if round_num not in (None, "", "all"):
            try:
                round_filter = int(round_num)
                lines = [r for r in lines if nostock_round_by_oid.get((r.get("order_id") or "").strip()) == round_filter]
            except:
                pass

        out = []
        for r in lines:
            oid = (r.get("order_id") or "").strip()
            out.append({
                "platform":      r.get("platform"),
                "store":         r.get("shop"),
                "order_no":      oid,
                "sku":           r.get("sku"),
                "brand":         r.get("brand"),
                "product_name":  r.get("model"),
                "stock":         int(r.get("stock_qty", 0) or 0),
                "qty":           int(r.get("qty", 0) or 0),
                "order_time":    r.get("order_time"),
                "due_date":      r.get("due_date"),
                "sla":           r.get("sla"),
                "shipping_type": r.get("logistic"),
                "assign_round":  nostock_round_by_oid.get(oid, r.get("nostock_round")),
                "printed_count": 0,
            })
        
        from collections import defaultdict
        sum_by_sku = defaultdict(int)
        for r in out:
            sum_by_sku[(r["sku"] or "").strip()] += int(r["qty"] or 0)
        for r in out:
            r["allqty"] = sum_by_sku[(r["sku"] or "").strip()]

        # เรียง
        sort_col = sort_col if sort_col in {"platform","store","order_no","sku","brand","product_name","stock","qty","allqty","order_time","due_date","sla","shipping_type","assign_round","printed_count"} else "order_no"
        rev = (sort_dir == "desc")
        def _key(v):
            if sort_col in {"stock","qty","allqty","assign_round","printed_count"}:
                try: return int(v.get(sort_col) or 0)
                except: return 0
            elif sort_col in {"order_time","due_date"}:
                try: return datetime.fromisoformat(str(v.get(sort_col)))
                except: return str(v.get(sort_col) or "")
            else:
                return str(v.get(sort_col) or "")
        out.sort(key=_key, reverse=rev)

        order_ids = sorted({(r["order_no"] or "").strip() for r in out if r.get("order_no")})
        counts_nostock = _get_print_counts_local(order_ids, "nostock")
        for r in out:
            oid = (r.get("order_no") or "").strip()
            r["printed_count"] = int(counts_nostock.get(oid, 0))

        # ดึงเวลาพิมพ์จาก DB
        sql_ts = text(f"""
            SELECT order_id, MAX(printed_nostock_at) AS ts
            FROM {tbl}
            WHERE printed_nostock > 0
              AND order_id IN :oids
            GROUP BY order_id
        """).bindparams(bindparam("oids", expanding=True))
        rows_ts = db.session.execute(sql_ts, {"oids": order_ids}).all() if order_ids else []
        ts_map = {}
        for row_ts in rows_ts:
            if not row_ts or not row_ts[0] or not row_ts[1]:
                continue
            oid_str = str(row_ts[0]).strip()
            ts_str = row_ts[1]
            try:
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is None:
                    dt = TH_TZ.localize(dt)
                ts_map[oid_str] = dt
            except Exception:
                pass

        for r in out:
            r["printed_at"] = ts_map.get((r.get("order_no") or "").strip())

        meta_printed_at = max(ts_map.values()) if ts_map else None

        # ดึงค่า nostock_round จาก DB
        if order_ids:
            sql = text(f"SELECT order_id, MAX(nostock_round) AS r FROM {tbl} WHERE order_id IN :oids GROUP BY order_id")
            sql = sql.bindparams(bindparam("oids", expanding=True))
            try:
                q_round = db.session.execute(sql, {"oids": order_ids}).all()
                round_map = {str(r[0]): (int(r[1]) if r[1] is not None else None) for r in q_round}
                for r in out:
                    oid = (r.get("order_no") or "").strip()
                    if oid in round_map and round_map[oid] is not None:
                        r["assign_round"] = round_map[oid]
            except Exception:
                pass

        if round_num and round_num != "all":
            try:
                r_int = int(round_num)
                out = [r for r in out if r.get("assign_round") == r_int]
                order_ids = sorted({(r["order_no"] or "").strip() for r in out if r.get("order_no")})
            except:
                pass

        logistics = sorted(set([r.get("shipping_type") for r in out if r.get("shipping_type")]))
        nostock_skus = {(r["sku"] or "").strip() for r in out if r.get("sku")}

        return render_template(
            "report_nostock.html",
            rows=out,
            summary={"sku_count": len(nostock_skus), "orders_count": len(order_ids)},
            printed_at=meta_printed_at,
            order_ids=order_ids,
            shops=shops,
            logistics=logistics,
            platform_sel=platform,
            shop_sel=shop_id,
            logistic_sel=logistic,
            is_history_view=True,
            available_dates=_available_dates(),
            print_date_sel=print_date,
            sort_col=sort_col,
            sort_dir=sort_dir,
            q="",
            round_sel=round_num
        )

    @app.route("/report/nostock.xlsx", methods=["GET"])
    @login_required
    def report_nostock_export():
        """Export Excel รายงานไม่มีสินค้า"""
        from services.lowstock import get_lowstock_rows_from_allocation
        import pandas as pd
        
        platform = normalize_platform(request.args.get("platform"))
        shop_id = request.args.get("shop_id")
        
        filters = {"platform": platform or None, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)
        rows = _filter_out_issued_rows(rows)
        
        safe = []
        for r in rows:
            r = dict(r)
            if (str(r.get("sales_status") or "")).upper() == "PACKED":
                continue
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try: stock_qty = int(prod.stock_qty or 0)
                        except: stock_qty = 0
                r["stock_qty"] = stock_qty
            safe.append(r)
        
        low_items = get_lowstock_rows_from_allocation(safe)
        nostock_skus = {(it.get("sku") or "").strip() for it in low_items 
                       if it.get("sku") and int(it.get("stock_qty", 0) or 0) == 0}
        lines = [r for r in safe if (r.get("sku") or "").strip() in nostock_skus]
        
        df = pd.DataFrame([{
            "แพลตฟอร์ม": r.get("platform"),
            "ร้าน": r.get("shop"),
            "เลข Order": r.get("order_id"),
            "SKU": r.get("sku"),
            "Brand": r.get("brand"),
            "ชื่อสินค้า": r.get("model"),
            "Stock": int(r.get("stock_qty", 0) or 0),
            "Qty": int(r.get("qty", 0) or 0),
            "เวลาที่ลูกค้าสั่ง": r.get("order_time"),
            "กำหนดส่ง": r.get("due_date"),
            "ประเภทขนส่ง": r.get("logistic"),
        } for r in lines])
        
        out = BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as w:
            df.to_excel(w, index=False, sheet_name="NoStock")
        out.seek(0)
        return send_file(out, as_attachment=True, download_name="report_nostock.xlsx")

    # ================== NEW: Update No Stock Round ==================
    @app.route("/report/nostock/update_round", methods=["POST"])
    @login_required
    def update_nostock_round():
        """อัปเดตรอบสำหรับรายงานไม่มีสินค้า"""
        data = request.get_json() or {}
        order_ids = data.get("order_ids", [])
        round_num = data.get("round")
        
        if not order_ids or round_num is None:
            return jsonify({"success": False, "message": "ข้อมูลไม่ครบ"})
        
        try:
            round_int = int(round_num)
        except:
            return jsonify({"success": False, "message": "รอบต้องเป็นตัวเลข"})
        
        tbl = _ol_table_name()
        sql = text(f"UPDATE {tbl} SET nostock_round = :r WHERE order_id IN :oids")
        sql = sql.bindparams(bindparam("oids", expanding=True))
        db.session.execute(sql, {"r": round_int, "oids": order_ids})
        db.session.commit()
        
        return jsonify({"success": True, "message": f"อัปเดตรอบเป็น {round_int} สำเร็จ ({len(order_ids)} ออเดอร์)"})
    # ================== /NEW ==================

    # -----------------------
    # Picking (รวมยอดหยิบ)
    # -----------------------
    def _aggregate_picking(rows: list[dict]) -> list[dict]:
        rows = rows or []
        agg: dict[str, dict] = {}
        for r in rows:
            if not bool(r.get("accepted")):
                continue
            if (r.get("allocation_status") or "") not in ("ACCEPTED", "READY_ACCEPT"):
                continue
            sku = str(r.get("sku") or "").strip()
            if not sku:
                continue
            brand = str(r.get("brand") or "").strip()
            model = str(r.get("model") or "").strip()
            qty = int(r.get("qty", 0) or 0)
            stock_qty = int(r.get("stock_qty", 0) or 0)
            dispatch_round = r.get("dispatch_round")
            
            a = agg.setdefault(sku, {
                "sku": sku, 
                "brand": brand, 
                "model": model, 
                "need_qty": 0, 
                "stock_qty": 0,
                "dispatch_rounds": set()
            })
            a["need_qty"] += qty
            if stock_qty > a["stock_qty"]:
                a["stock_qty"] = stock_qty
            if dispatch_round is not None:
                a["dispatch_rounds"].add(dispatch_round)

        items = []
        for _, a in agg.items():
            need = int(a["need_qty"])
            stock = int(a["stock_qty"])
            shortage = max(0, need - stock)
            remain = stock - need
            
            # Handle dispatch_round display
            dispatch_rounds = sorted(a["dispatch_rounds"])
            if len(dispatch_rounds) == 0:
                dispatch_round_display = None
            elif len(dispatch_rounds) == 1:
                dispatch_round_display = dispatch_rounds[0]
            else:
                dispatch_round_display = f"{dispatch_rounds[0]}-{dispatch_rounds[-1]}"
            
            items.append({
                "sku": a["sku"], 
                "brand": a["brand"], 
                "model": a["model"],
                "need_qty": need, 
                "stock_qty": stock, 
                "shortage": shortage, 
                "remain_after_pick": remain,
                "dispatch_round": dispatch_round_display,
            })
        items.sort(key=lambda x: (x["brand"].lower(), x["model"].lower(), x["sku"].lower()))
        return items

    @app.route("/report/picking", methods=["GET"])
    @login_required
    def picking_list():
        platform = normalize_platform(request.args.get("platform"))
        shop_id = request.args.get("shop_id")
        logistic = request.args.get("logistic")

        filters = {"platform": platform if platform else None, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)

        # *** กรองออเดอร์ที่พิมพ์ Picking แล้วออก - แสดงเฉพาะที่ยังไม่ได้พิมพ์ ***
        rows = [r for r in rows if (r.get("printed_picking") or 0) == 0]

        # เตรียมข้อมูลปลอดภัย + ใส่ stock_qty ให้ครบ
        safe_rows = []
        for r in rows:
            r = dict(r)
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try:
                            stock_qty = int(prod.stock_qty or 0)
                        except Exception:
                            stock_qty = 0
                    else:
                        st = Stock.query.filter_by(sku=sku).first()
                        stock_qty = int(st.qty) if st and st.qty is not None else 0
                r["stock_qty"] = stock_qty
            r["accepted"] = bool(r.get("accepted", False))
            r["sales_status"] = r.get("sales_status", None)
            r["logistic"] = r.get("logistic") or r.get("logistic_type") or "-"
            safe_rows.append(r)

        if logistic:
            safe_rows = [r for r in safe_rows if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]

        # รวมต่อ SKU
        items = _aggregate_picking(safe_rows)

        # ===== นับจำนวนครั้งที่พิมพ์ Picking (รวมทั้งชุดงาน) — ใช้ MAX ไม่ใช่ SUM =====
        valid_rows = [r for r in safe_rows if r.get("accepted") and r.get("allocation_status") in ("ACCEPTED", "READY_ACCEPT")]
        order_ids = sorted({(r.get("order_id") or "").strip() for r in valid_rows if r.get("order_id")})
        print_counts_pick = _get_print_counts_local(order_ids, "picking")
        print_count_overall = max(print_counts_pick.values()) if print_counts_pick else 0
        
        # Get the latest print timestamp and user
        print_timestamp_overall = None
        print_user_overall = None
        if order_ids:
            tbl = _ol_table_name()
            sql = text(f"SELECT printed_picking_at, printed_picking_by FROM {tbl} WHERE order_id IN :oids AND printed_picking_at IS NOT NULL ORDER BY printed_picking_at DESC LIMIT 1")
            sql = sql.bindparams(bindparam("oids", expanding=True))
            result = db.session.execute(sql, {"oids": order_ids}).first()
            if result:
                try:
                    dt = datetime.fromisoformat(result[0])
                    if dt.tzinfo is None:
                        dt = TH_TZ.localize(dt)
                    print_timestamp_overall = dt
                    print_user_overall = result[1]  # username
                except Exception:
                    pass

        # ชื่อร้านสำหรับแสดงในคอลัมน์ใหม่
        shop_sel_name = None
        if shop_id:
            s = Shop.query.get(int(shop_id))
            if s:
                shop_sel_name = f"{s.platform} • {s.name}"

        # เติมแพลตฟอร์ม/ร้าน/ประเภทขนส่งให้แต่ละ item เพื่อไม่ให้ขึ้น '-'
        for it in items:
            it["platform"] = platform or "-"
            it["shop"] = shop_sel_name or "-"
            it["logistic"] = logistic or "-"

        totals = {
            "total_skus": len(items),
            "total_need_qty": sum(i["need_qty"] for i in items),
            "total_shortage": sum(i["shortage"] for i in items),
        }
        shops = Shop.query.order_by(Shop.name.asc()).all()
        logistics = sorted(set(r.get("logistic") for r in safe_rows if r.get("logistic")))

        return render_template(
            "picking.html",
            items=items,
            totals=totals,
            shops=shops,
            logistics=logistics,
            platform_sel=platform,
            shop_sel=shop_id,
            shop_sel_name=shop_sel_name,
            logistic_sel=logistic,
            official_print=False,
            printed_meta=None,
            print_count_overall=print_count_overall,
            print_timestamp_overall=print_timestamp_overall,
            print_user_overall=print_user_overall,
            order_ids=order_ids,  # Pass order IDs for dispatch round update
        )

    @app.route("/report/picking/print", methods=["POST"])
    @login_required
    def picking_list_commit():
        cu = current_user()
        platform = normalize_platform(request.form.get("platform"))
        shop_id = request.form.get("shop_id")
        logistic = request.form.get("logistic")
        override = request.form.get("override") in ("1", "true", "yes")
        
        # Get selected order IDs from form (comma-separated)
        # ถ้าเป็น '', 'all', 'ALL' ให้ถือว่า "ไม่ระบุ"
        order_ids_raw = (request.form.get("order_ids") or "").strip()
        selected_order_ids = [] if order_ids_raw.lower() in ("", "all") else \
            [oid.strip() for oid in order_ids_raw.split(",") if oid.strip()]

        filters = {"platform": platform if platform else None, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)

        safe_rows = []
        for r in rows:
            r = dict(r)
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try:
                            stock_qty = int(prod.stock_qty or 0)
                        except Exception:
                            stock_qty = 0
                    else:
                        st = Stock.query.filter_by(sku=sku).first()
                        stock_qty = int(st.qty) if st and st.qty is not None else 0
                r["stock_qty"] = stock_qty
            r["accepted"] = bool(r.get("accepted", False))
            r["logistic"] = r.get("logistic") or r.get("logistic_type") or "-"
            safe_rows.append(r)

        if logistic:
            safe_rows = [r for r in safe_rows if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]

        valid_rows = [r for r in safe_rows if r.get("accepted") and r.get("allocation_status") in ("ACCEPTED", "READY_ACCEPT")]
        
        # If specific order IDs were selected, filter to only those
        if selected_order_ids:
            valid_rows = [r for r in valid_rows if (r.get("order_id") or "").strip() in selected_order_ids]
            oids = sorted(selected_order_ids)
        else:
            oids = sorted({(r.get("order_id") or "").strip() for r in valid_rows if r.get("order_id")})

        if not oids:
            flash("ไม่พบออเดอร์สำหรับพิมพ์ Picking", "warning")
            return redirect(url_for("picking_list", platform=platform, shop_id=shop_id, logistic=logistic))

        already = _detect_already_printed(oids, kind="picking")
        if already and not (override and cu and cu.role == "admin"):
            head = ", ".join(list(already)[:10])
            more = "" if len(already) <= 10 else f" ... (+{len(already)-10})"
            flash(f"มีบางออเดอร์เคยพิมพ์ Picking ไปแล้ว: {head}{more}", "danger")
            flash("ถ้าจำเป็นต้องพิมพ์ซ้ำ โปรดให้แอดมินติ๊ก 'อนุญาตพิมพ์ซ้ำ' แล้วพิมพ์อีกครั้ง", "warning")
            return redirect(url_for("picking_list", platform=platform, shop_id=shop_id, logistic=logistic))

        now_iso = now_thai().isoformat()
        _mark_printed(oids, kind="picking", user_id=(cu.id if cu else None), when_iso=now_iso)
        
        # >>> NEW: ย้ายไป Orderจ่ายแล้ว (บันทึกเวลาตอนพิมพ์)
        _mark_issued(oids, user_id=(cu.id if cu else None), source="print:picking", when_dt=now_thai())
        
        db.session.commit()  # Ensure changes are committed
        db.session.expire_all()  # Force refresh to get updated print counts

        items = _aggregate_picking(safe_rows)
        for it in items:
            it["platform"] = platform or "-"
            if shop_id:
                s = Shop.query.get(int(shop_id))
                it["shop"] = (f"{s.platform} • {s.name}") if s else "-"
            else:
                it["shop"] = "-"
            it["logistic"] = logistic or "-"

        totals = {
            "total_skus": len(items),
            "total_need_qty": sum(i["need_qty"] for i in items),
            "total_shortage": sum(i["shortage"] for i in items),
        }
        shops = Shop.query.order_by(Shop.name.asc()).all()
        logistics = sorted(set(r.get("logistic") for r in safe_rows if r.get("logistic")))
        printed_meta = {"by": (cu.username if cu else "-"), "at": now_thai(), "orders": len(oids), "override": bool(already)}

        print_counts_pick = _get_print_counts_local(oids, "picking")
        print_count_overall = max(print_counts_pick.values()) if print_counts_pick else 0
        
        # Use current timestamp and user
        print_timestamp_overall = now_thai()
        print_user_overall = cu.username if cu else None

        shop_sel_name = None
        if shop_id:
            s = Shop.query.get(int(shop_id))
            if s:
                shop_sel_name = f"{s.platform} • {s.name}"

        return render_template(
            "picking.html",
            items=items,
            totals=totals,
            shops=shops,
            logistics=logistics,
            platform_sel=platform,
            shop_sel=shop_id,
            shop_sel_name=shop_sel_name,
            logistic_sel=logistic,
            official_print=True,
            printed_meta=printed_meta,
            print_count_overall=print_count_overall,
            print_timestamp_overall=print_timestamp_overall,
            print_user_overall=print_user_overall,
            order_ids=oids,  # Pass order IDs for dispatch round update
        )

    # ================== NEW: View Printed Picking Lists ==================
    @app.route("/report/picking/printed", methods=["GET"])
    @login_required
    def picking_printed_history():
        """ดู Picking List ที่พิมพ์แล้ว - สามารถเลือกวันที่และพิมพ์ซ้ำได้"""
        platform = normalize_platform(request.args.get("platform"))
        shop_id = request.args.get("shop_id")
        logistic = request.args.get("logistic")
        print_date = request.args.get("print_date")  # วันที่พิมพ์ (YYYY-MM-DD)
        
        # Get all orders that have been printed for picking
        tbl = _ol_table_name()
        
        # Build query to get orders with print history
        if print_date:
            # Filter by specific print date
            try:
                target_date = datetime.strptime(print_date, "%Y-%m-%d").date()
                sql = text(f"""
                    SELECT DISTINCT order_id 
                    FROM {tbl} 
                    WHERE printed_picking > 0 
                    AND DATE(printed_picking_at) = :target_date
                """)
                result = db.session.execute(sql, {"target_date": target_date.isoformat()}).fetchall()
            except:
                result = []
        else:
            # Get all printed orders
            sql = text(f"SELECT DISTINCT order_id FROM {tbl} WHERE printed_picking > 0")
            result = db.session.execute(sql).fetchall()
        
        printed_order_ids = [row[0] for row in result if row[0]]
        
        if not printed_order_ids:
            # No printed orders found
            shops = Shop.query.order_by(Shop.name.asc()).all()
            return render_template(
                "picking.html",
                items=[],
                totals={"total_skus": 0, "total_need_qty": 0, "total_shortage": 0},
                shops=shops,
                logistics=[],
                platform_sel=platform,
                shop_sel=shop_id,
                shop_sel_name=None,
                logistic_sel=logistic,
                official_print=False,
                printed_meta=None,
                print_count_overall=0,
                print_timestamp_overall=None,
                order_ids=[],
                is_history_view=True,
                print_date_sel=print_date,
                available_dates=[]
            )
        
        # Get full data for these orders
        filters = {"platform": platform if platform else None, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)
        
        # Filter to only printed orders
        safe_rows = []
        for r in rows:
            if (r.get("order_id") or "").strip() not in printed_order_ids:
                continue
            r = dict(r)
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try:
                            stock_qty = int(prod.stock_qty or 0)
                        except Exception:
                            stock_qty = 0
                    else:
                        st = Stock.query.filter_by(sku=sku).first()
                        stock_qty = int(st.qty) if st and st.qty is not None else 0
                r["stock_qty"] = stock_qty
            r["accepted"] = bool(r.get("accepted", False))
            r["sales_status"] = r.get("sales_status", None)
            r["logistic"] = r.get("logistic") or r.get("logistic_type") or "-"
            safe_rows.append(r)
        
        if logistic:
            safe_rows = [r for r in safe_rows if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]
        
        # Aggregate by SKU
        items = _aggregate_picking(safe_rows)
        
        # Get print counts
        valid_rows = [r for r in safe_rows if r.get("accepted") and r.get("allocation_status") in ("ACCEPTED", "READY_ACCEPT")]
        order_ids = sorted({(r.get("order_id") or "").strip() for r in valid_rows if r.get("order_id")})
        print_counts_pick = _get_print_counts_local(order_ids, "picking")
        print_count_overall = max(print_counts_pick.values()) if print_counts_pick else 0
        
        # Get the latest print timestamp and user
        print_timestamp_overall = None
        print_user_overall = None
        if order_ids:
            sql = text(f"SELECT printed_picking_at, printed_picking_by FROM {tbl} WHERE order_id IN :oids AND printed_picking_at IS NOT NULL ORDER BY printed_picking_at DESC LIMIT 1")
            sql = sql.bindparams(bindparam("oids", expanding=True))
            result = db.session.execute(sql, {"oids": order_ids}).first()
            if result:
                try:
                    dt = datetime.fromisoformat(result[0])
                    if dt.tzinfo is None:
                        dt = TH_TZ.localize(dt)
                    print_timestamp_overall = dt
                    print_user_overall = result[1]
                except Exception:
                    pass
        
        # Shop name
        shop_sel_name = None
        if shop_id:
            s = Shop.query.get(int(shop_id))
            if s:
                shop_sel_name = f"{s.platform} • {s.name}"
        
        # Fill in platform/shop/logistic for each item
        for it in items:
            it["platform"] = platform or "-"
            it["shop"] = shop_sel_name or "-"
            it["logistic"] = logistic or "-"
        
        totals = {
            "total_skus": len(items),
            "total_need_qty": sum(i["need_qty"] for i in items),
            "total_shortage": sum(i["shortage"] for i in items),
        }
        shops = Shop.query.order_by(Shop.name.asc()).all()
        logistics = sorted(set(r.get("logistic") for r in safe_rows if r.get("logistic")))
        
        # Get available print dates for dropdown
        sql_dates = text(f"""
            SELECT DISTINCT DATE(printed_picking_at) as print_date 
            FROM {tbl} 
            WHERE printed_picking > 0 AND printed_picking_at IS NOT NULL
            ORDER BY print_date DESC
        """)
        available_dates = [row[0] for row in db.session.execute(sql_dates).fetchall()]
        
        return render_template(
            "picking.html",
            items=items,
            totals=totals,
            shops=shops,
            logistics=logistics,
            platform_sel=platform,
            shop_sel=shop_id,
            shop_sel_name=shop_sel_name,
            logistic_sel=logistic,
            official_print=False,
            printed_meta=None,
            print_count_overall=print_count_overall,
            print_timestamp_overall=print_timestamp_overall,
            print_user_overall=print_user_overall,
            order_ids=order_ids,
            is_history_view=True,
            print_date_sel=print_date,
            available_dates=available_dates
        )

    @app.route("/export_picking.xlsx")
    @login_required
    def export_picking_excel():
        platform = normalize_platform(request.args.get("platform"))
        shop_id = request.args.get("shop_id")
        logistic = request.args.get("logistic")

        filters = {"platform": platform if platform else None, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = _filter_out_cancelled_rows(rows)

        safe_rows = []
        for r in rows:
            r = dict(r)
            if "stock_qty" not in r:
                sku = (r.get("sku") or "").strip()
                stock_qty = 0
                if sku:
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod and hasattr(prod, "stock_qty"):
                        try:
                            stock_qty = int(prod.stock_qty or 0)
                        except Exception:
                            stock_qty = 0
                    else:
                        st = Stock.query.filter_by(sku=sku).first()
                        stock_qty = int(st.qty) if st and st.qty is not None else 0
                r["stock_qty"] = stock_qty
            r["accepted"] = bool(r.get("accepted", False))
            r["logistic"] = r.get("logistic") or r.get("logistic_type") or "-"
            safe_rows.append(r)

        if logistic:
            safe_rows = [r for r in safe_rows if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]

        items = _aggregate_picking(safe_rows)

        valid_rows = [r for r in safe_rows if r.get("accepted") and r.get("allocation_status") in ("ACCEPTED", "READY_ACCEPT")]
        order_ids = sorted({(r.get("order_id") or "").strip() for r in valid_rows if r.get("order_id")})
        print_counts_pick = _get_print_counts_local(order_ids, "picking")
        print_count_overall = max(print_counts_pick.values()) if print_counts_pick else 0

        shop_name = ""
        if shop_id:
            s = Shop.query.get(int(shop_id))
            if s:
                shop_name = f"{s.platform} • {s.name}"

        for it in items:
            it["platform"] = platform or ""
            it["shop_name"] = shop_name or ""
            it["logistic"] = logistic or ""

        df = pd.DataFrame([{
            "แพลตฟอร์ม": it["platform"],
            "ร้าน": it["shop_name"],
            "SKU": it["sku"],
            "Brand": it["brand"],
            "สินค้า": it["model"],
            "ต้องหยิบ": it["need_qty"],
            "สต็อก": it["stock_qty"],
            "ขาด": it["shortage"],
            "คงเหลือหลังหยิบ": it["remain_after_pick"],
            "ประเภทขนส่ง": it["logistic"],
            "พิมพ์แล้ว (ครั้ง)": print_count_overall,
        } for it in items])

        out = BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as w:
            df.to_excel(w, index=False, sheet_name="Picking List")
        out.seek(0)
        return send_file(out, as_attachment=True, download_name="picking_list.xlsx")

    # -----------------------
    # ดาวน์โหลด Orders Excel Template (เดิม)
    # -----------------------
    @app.route("/download/orders-template")
    @login_required
    def download_orders_template():
        platform = normalize_platform(request.args.get("platform") or "Shopee")
        cols = ["ชื่อร้าน", "Order ID", "SKU", "Item Name", "Qty", "Order Time", "Logistics"]

        sample = pd.DataFrame(columns=cols)
        sample.loc[0] = ["Your Shop", "ORDER123", "SKU-001", "สินค้าทดลอง", 1, "2025-01-01 12:00", "J&T"]

        out = BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
            sample.to_excel(writer, index=False, sheet_name=f"{platform} Orders")
        out.seek(0)
        return send_file(out, as_attachment=True, download_name=f"{platform}_Orders_Template.xlsx")

    # -----------------------
    # Admin clear
    # -----------------------
    @app.route("/admin/clear", methods=["GET","POST"])
    @login_required
    def admin_clear():
        cu = current_user()
        if not cu or cu.role != "admin":
            flash("เฉพาะแอดมินเท่านั้นที่สามารถล้างข้อมูลได้", "danger")
            return redirect(url_for("dashboard"))
        
        if request.method == "POST":
            scope = request.form.get("scope")
            
            if scope == "today":
                today = now_thai().date()
                deleted = OrderLine.query.filter(OrderLine.import_date == today).delete()
                db.session.commit()
                flash(f"ลบข้อมูลของวันนี้แล้ว ({deleted} รายการ)", "warning")
                
            elif scope == "all":
                deleted = OrderLine.query.delete()
                db.session.commit()
                flash(f"ลบข้อมูลออเดอร์ทั้งหมดแล้ว ({deleted} รายการ)", "danger")
                
            elif scope == "cancelled":
                # Get all cancelled order IDs
                cancelled_orders = CancelledOrder.query.all()
                cancelled_order_ids = [co.order_id for co in cancelled_orders]
                
                if cancelled_order_ids:
                    # Delete OrderLine records
                    deleted_lines = OrderLine.query.filter(
                        OrderLine.order_id.in_(cancelled_order_ids)
                    ).delete(synchronize_session=False)
                    
                    # Delete CancelledOrder records
                    deleted_cancelled = CancelledOrder.query.delete()
                    
                    db.session.commit()
                    flash(f"ลบ Order ยกเลิกทั้งหมดแล้ว ({len(cancelled_order_ids)} ออเดอร์, {deleted_lines} รายการ)", "warning")
                else:
                    flash("ไม่พบ Order ยกเลิก", "info")
                    
            elif scope == "issued":
                # Get all issued order IDs
                issued_orders = IssuedOrder.query.all()
                issued_order_ids = [io.order_id for io in issued_orders]
                
                if issued_order_ids:
                    # Delete OrderLine records
                    deleted_lines = OrderLine.query.filter(
                        OrderLine.order_id.in_(issued_order_ids)
                    ).delete(synchronize_session=False)
                    
                    # Delete IssuedOrder records
                    deleted_issued = IssuedOrder.query.delete()
                    
                    db.session.commit()
                    flash(f"ลบ Order จ่ายแล้วทั้งหมดแล้ว ({len(issued_order_ids)} ออเดอร์, {deleted_lines} รายการ)", "warning")
                else:
                    flash("ไม่พบ Order จ่ายแล้ว", "info")
            
            return redirect(url_for("admin_clear"))
        
        # GET request - show stats
        today = now_thai().date()
        stats = {
            "total_orders": db.session.query(func.count(func.distinct(OrderLine.order_id))).scalar() or 0,
            "cancelled_orders": CancelledOrder.query.count(),
            "issued_orders": IssuedOrder.query.count(),
            "today_orders": db.session.query(func.count(func.distinct(OrderLine.order_id))).filter(
                OrderLine.import_date == today
            ).scalar() or 0,
        }
        
        return render_template("clear_confirm.html", stats=stats)

    return app


app = create_app()

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", "8000"))
    serve(app, host="0.0.0.0", port=port)