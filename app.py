
# app.py
from __future__ import annotations

import os
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

    # ---------- Helper: Table name (OrderLine) ----------
    def _ol_table_name() -> str:
        try:
            return OrderLine.__table__.name
        except Exception:
            return getattr(OrderLine, "__tablename__", "order_lines")

    # ---------- Auto-migrate: ensure print columns exist ----------
    def _ensure_orderline_print_columns():
        tbl = _ol_table_name()
        with db.engine.connect() as con:
            cols = {row[1] for row in con.execute(text(f"PRAGMA table_info({tbl})")).fetchall()}

            def add(col, ddl):
                if col not in cols:
                    con.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col} {ddl}"))

            # สำหรับ "ใบงานคลัง"
            add("printed_warehouse", "INTEGER DEFAULT 0")
            add("printed_warehouse_count", "INTEGER DEFAULT 0")
            add("printed_warehouse_at", "TEXT")
            add("printed_warehouse_by_user_id", "INTEGER")

            # สำหรับ "Picking list"
            add("printed_picking", "INTEGER DEFAULT 0")
            add("printed_picking_count", "INTEGER DEFAULT 0")
            add("printed_picking_at", "TEXT")
            add("printed_picking_by_user_id", "INTEGER")

            con.commit()

    with app.app_context():
        db.create_all()
        _ensure_orderline_print_columns()
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
        sql = text(f"SELECT DISTINCT order_id FROM {tbl} WHERE order_id IN :oids AND {col}=1")
        sql = sql.bindparams(bindparam("oids", expanding=True))
        rows = db.session.execute(sql, {"oids": oids}).scalars().all()
        return set(r for r in rows if r)

    def _mark_printed(oids: list[str], kind: str, user_id: int | None, when_iso: str):
        if not oids:
            return
        tbl = _ol_table_name()
        if kind == "warehouse":
            col_flag = "printed_warehouse"
            col_cnt  = "printed_warehouse_count"
            col_at   = "printed_warehouse_at"
            col_by   = "printed_warehouse_by_user_id"
        else:
            col_flag = "printed_picking"
            col_cnt  = "printed_picking_count"
            col_at   = "printed_picking_at"
            col_by   = "printed_picking_by_user_id"

        sql = text(
            f"""
            UPDATE {tbl}
               SET {col_flag}=1,
                   {col_cnt}=COALESCE({col_cnt},0)+1,
                   {col_by}=:uid,
                   {col_at}=:ts
             WHERE order_id IN :oids
            """
        ).bindparams(bindparam("oids", expanding=True))
        db.session.execute(sql, {"uid": user_id, "ts": when_iso, "oids": oids})
        db.session.commit()

    # --------------------------
    # Print count helpers (ใหม่)
    # --------------------------
    def _get_print_counts_local(oids: list[str], kind: str) -> dict[str, int]:
        """คืน dict: {order_id: count} ใช้ get_print_counts ถ้ามี ไม่งั้นอ่านจาก *_count"""
        try:
            if "get_print_counts" in globals() and callable(globals()["get_print_counts"]):
                res = get_print_counts(oids, kind) or {}
                if isinstance(res, dict):
                    return {str(k): int(v or 0) for k, v in res.items()}
        except Exception:
            pass
        if not oids:
            return {}
        tbl = _ol_table_name()
        col = "printed_warehouse_count" if kind == "warehouse" else "printed_picking_count"
        sql = text(f"SELECT order_id, COALESCE(MAX({col}),0) AS c FROM {tbl} WHERE order_id IN :oids GROUP BY order_id")
        sql = sql.bindparams(bindparam("oids", expanding=True))
        rows_sql = db.session.execute(sql, {"oids": oids}).all()
        return {str(r[0]): int(r[1] or 0) for r in rows_sql if r and r[0]}

    def _inject_print_counts_to_rows(rows: list[dict], kind: str):
        """ฝัง printed_*_count ลงในแต่ละแถว (ใช้กับ Warehouse report)"""
        oids = sorted({(r.get("order_id") or "").strip() for r in rows if r.get("order_id")})
        counts = _get_print_counts_local(oids, kind)
        for r in rows:
            oid = (r.get("order_id") or "").strip()
            c = int(counts.get(oid, 0))
            r["printed_count"] = c
            if kind == "warehouse":
                r["printed_warehouse_count"] = c
            else:
                r["printed_picking_count"] = c

    # -------------
    # Routes: Auth & Users
    # -------------
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
        )

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
            if not platform or not shop_name or not f:
                flash("กรุณาเลือกแพลตฟอร์ม / ชื่อร้าน และเลือกไฟล์", "danger")
                return redirect(url_for("import_orders_view"))
            try:
                df = pd.read_excel(f)
                imported, updated = import_orders(
                    df, platform=platform, shop_name=shop_name, import_date=now_thai().date()
                )
                flash(f"นำเข้าออเดอร์สำเร็จ: เพิ่ม {imported} อัปเดต {updated}", "success")
                return redirect(url_for("dashboard", import_date=now_thai().date().isoformat()))
            except Exception as e:
                flash(f"เกิดข้อผิดพลาดในการนำเข้าออเดอร์: {e}", "danger")
                return redirect(url_for("import_orders_view"))
        shops = Shop.query.order_by(Shop.name.asc()).all()
        return render_template("import_orders.html", shops=shops)

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
        rows = [r for r in rows if r.get("accepted") and r.get("allocation_status") in ("ACCEPTED", "READY_ACCEPT")]

        if logistic:
            rows = [r for r in rows if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]

        # >>> ฝังจำนวนครั้งที่พิมพ์ต่อออเดอร์
        _inject_print_counts_to_rows(rows, kind="warehouse")

        rows = _group_rows_for_report(rows)

        total_orders = len({(r.get("order_id") or "").strip() for r in rows if r.get("order_id")})
        total_qty = sum(int(r.get("qty", 0) or 0) for r in rows)
        shops = Shop.query.all()
        logistics = sorted(set(r.get("logistic") for r in rows if r.get("logistic")))
        return render_template(
            "report.html",
            rows=rows,
            count_orders=total_orders,
            total_qty=total_qty,
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

        filters = {"platform": platform, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)
        rows = [r for r in rows if r.get("accepted") and r.get("allocation_status") in ("ACCEPTED", "READY_ACCEPT")]

        if logistic:
            rows = [r for r in rows if (r.get("logistic") or "").lower().find(logistic.lower()) >= 0]

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

        # >>> ฝังจำนวนครั้งที่พิมพ์ (หลัง mark แล้ว)
        _inject_print_counts_to_rows(rows, kind="warehouse")

        rows = _group_rows_for_report(rows)
        total_orders = len({(r.get("order_id") or "").strip() for r in rows if r.get("order_id")})
        total_qty = sum(int(r.get("qty", 0) or 0) for r in rows)
        shops = Shop.query.all()
        logistics = sorted(set(r.get("logistic") for r in rows if r.get("logistic")))
        printed_meta = {"by": (cu.username if cu else "-"), "at": now_thai(), "orders": total_orders, "override": bool(already)}
        return render_template(
            "report.html",
            rows=rows,
            count_orders=total_orders,
            total_qty=total_qty,
            shops=shops,
            logistics=logistics,
            platform_sel=platform,
            shop_sel=shop_id,
            logistic_sel=logistic,
            official_print=True,
            printed_meta=printed_meta
        )

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
            a = agg.setdefault(sku, {"sku": sku, "brand": brand, "model": model, "need_qty": 0, "stock_qty": 0})
            a["need_qty"] += qty
            if stock_qty > a["stock_qty"]:
                a["stock_qty"] = stock_qty

        items = []
        for _, a in agg.items():
            need = int(a["need_qty"])
            stock = int(a["stock_qty"])
            shortage = max(0, need - stock)
            remain = stock - need
            items.append({
                "sku": a["sku"], "brand": a["brand"], "model": a["model"],
                "need_qty": need, "stock_qty": stock, "shortage": shortage, "remain_after_pick": remain,
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
        )

    @app.route("/report/picking/print", methods=["POST"])
    @login_required
    def picking_list_commit():
        cu = current_user()
        platform = normalize_platform(request.form.get("platform"))
        shop_id = request.form.get("shop_id")
        logistic = request.form.get("logistic")
        override = request.form.get("override") in ("1", "true", "yes")

        filters = {"platform": platform if platform else None, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)

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
        oids = sorted({(r.get("order_id") or "").strip() for r in valid_rows if r.get("order_id")})

        # ป้องกันพิมพ์ซ้ำ
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

        # บันทึกการพิมพ์
        now_iso = now_thai().isoformat()
        _mark_printed(oids, kind="picking", user_id=(cu.id if cu else None), when_iso=now_iso)

        # รวมต่อ SKU สำหรับแสดงผล
        items = _aggregate_picking(safe_rows)
        for it in items:
            it["platform"] = platform or "-"
            # แสดงชื่อร้านสวยงาม
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

        # ดึงจำนวนครั้งที่พิมพ์ล่าสุด (หลัง mark แล้ว) — ใช้ MAX
        print_counts_pick = _get_print_counts_local(oids, "picking")
        print_count_overall = max(print_counts_pick.values()) if print_counts_pick else 0

        # ชื่อร้านสำหรับหัวกระดาษ
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
        )

    @app.route("/export_picking.xlsx")
    @login_required
    def export_picking_excel():
        platform = normalize_platform(request.args.get("platform"))
        shop_id = request.args.get("shop_id")
        logistic = request.args.get("logistic")

        filters = {"platform": platform if platform else None, "shop_id": int(shop_id) if shop_id else None, "import_date": None}
        rows, _ = compute_allocation(db.session, filters)

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

        # นับครั้งพิมพ์รวมของชุดงาน (ใช้ MAX)
        valid_rows = [r for r in safe_rows if r.get("accepted") and r.get("allocation_status") in ("ACCEPTED", "READY_ACCEPT")]
        order_ids = sorted({(r.get("order_id") or "").strip() for r in valid_rows if r.get("order_id")})
        print_counts_pick = _get_print_counts_local(order_ids, "picking")
        print_count_overall = max(print_counts_pick.values()) if print_counts_pick else 0

        # แปลงชื่อร้าน
        shop_name = ""
        if shop_id:
            s = Shop.query.get(int(shop_id))
            if s:
                shop_name = f"{s.platform} • {s.name}"

        # เติม platform/shop/logistic ใน items เพื่อให้ไฟล์มีข้อมูลครบ
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
    # Admin clear
    # -----------------------
    @app.route("/admin/clear", methods=["GET","POST"])
    @login_required
    def admin_clear():
        if request.method=="POST":
            scope = request.form.get("scope")
            if scope == "today":
                today = now_thai().date()
                OrderLine.query.filter(OrderLine.import_date==today).delete()
                db.session.commit()
                flash("ลบข้อมูลของวันนี้แล้ว", "warning")
            elif scope == "all":
                OrderLine.query.delete(); db.session.commit()
                flash("ลบข้อมูลออเดอร์ทั้งหมดแล้ว", "danger")
            return redirect(url_for("dashboard"))
        return render_template("clear_confirm.html")

    return app


app = create_app()

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", "8000"))
    serve(app, host="0.0.0.0", port=port)