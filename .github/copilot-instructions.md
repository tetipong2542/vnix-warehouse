## Quick context for AI coding agents

This repository is a small Flask-based internal order/warehouse management app tailored to Thai business rules (Thai timezone, Buddhist Era dates).

- Entry point: `app.py` — creates Flask app, initializes `SQLAlchemy` with a SQLite `data.db` next to the project, and auto-bootstraps an `admin` user if none exists.
- DB models: `models.py` (`Shop`, `Product`, `Stock`, `Sales`, `OrderLine`, `User`). Note `OrderLine` has a unique constraint on `(platform, shop_id, order_id, sku)` (`uq_orderline`).
- Import pipeline: `importers.py` exposes `import_products`, `import_stock`, `import_sales`, `import_orders`. These functions accept pandas DataFrames and normalize many column-name variants (Thai/EN). `import_orders` is INSERT-ONLY: if (order_id, sku) exists it will be skipped.
- Time and SLA logic: `utils.py` contains timezone (`TH_TZ`), robust datetime parsing (`parse_datetime_guess`), business-day-aware SLA/due-date functions (`compute_due_date`, `sla_text`, `add_business_days`). Use these helpers instead of ad-hoc date logic.

## Important patterns & rules (copy/paste friendly)

- Database: migrations are not managed with Alembic. `app.py` contains `_ensure_orderline_print_columns()` which runs `ALTER TABLE` PRAGMA logic at startup to add missing print-related columns — when adding lightweight columns, follow the same pattern.
- Order imports: `import_orders(df, platform, shop_name, import_date)` first normalizes columns with `first_existing(...)`, groups duplicate rows (order_id+sku) and sums qty, then inserts only new `OrderLine` rows. Do not convert this to an update-by-default flow unless intentionally changing semantics.
- Stock imports: `import_stock` coerces qty to int (NaN -> 0) and aggregates duplicate SKUs before updating `Stock` and (optionally) `Product.stock_qty` if present.
- Platform normalization: always call `normalize_platform()` (from `utils.py`) when matching platform names.
- Datetime handling: prefer `parse_datetime_guess()` to parse times from spreadsheets — it handles Excel serials, Unix ts, Thai BE dates, and rejects small numbers.

## Running & developer workflows (Windows)

- Recommended quick start (Windows): run the provided batch `run_server.bat`. It will create a venv (if missing), install `requirements.txt`, and run `python app.py` on port 8000 by default.
- Manual start: create/activate a Python venv, then `pip install -r requirements.txt` and `python app.py`.
- DB file: `data.db` is created in the project directory. Back it up before destructive schema changes.

## Quick guide for AI coding agents (repo-specific)

This is a small Flask-based order/warehouse app with Thai business rules (Thai timezone, Buddhist Era dates). Key facts and actionable patterns below so you can be productive quickly.

- Entry point: `app.py` — creates the Flask app, initializes SQLAlchemy, and auto-bootstraps an `admin` user if none exists. The DB file is `data.db` in the project root.
- Models: `models.py` defines `Shop`, `Product`, `Stock`, `Sales`, `OrderLine`, `User`. Note: `OrderLine` has a unique constraint `(platform, shop_id, order_id, sku)` (`uq_orderline`).
- Importers: `importers.py` exposes `import_products`, `import_stock`, `import_sales`, `import_orders`. These accept pandas DataFrames and normalize many header variants (Thai/EN). Important: `import_orders` is INSERT-ONLY — existing (order_id, sku) rows are skipped (no updates).

Critical patterns and conventions
- Datetime & SLA: use `utils.parse_datetime_guess()` for spreadsheet times (handles Excel serials, UNIX timestamps, Thai BE dates). Use `utils.sla_text`, `compute_due_date`, and `add_business_days` for SLA/due logic.
- Platform names: normalize with `utils.normalize_platform()` before matching/filtering.
- Importer behavior: aggregations happen before DB writes — `import_orders` groups by (order_id, sku) and sums qty; `import_stock` coerces qty to int and aggregates duplicate SKUs.
- DB schema changes: there is no Alembic. `app.py` contains an `_ensure_orderline_print_columns()` helper that uses PRAGMA/ALTER TABLE to add missing print columns at startup; follow this pattern for lightweight columns or document/implement a proper migration.

Developer workflows (Windows)
- Quick start: run `run_server.bat` (creates venv if missing, pip installs `requirements.txt`, and runs `python app.py` on port 8000).
- Manual: create/activate a venv, run `pip install -r requirements.txt`, then `python app.py`.
- DB caution: `data.db` lives in the repo root — back it up before destructive changes.

Templates & UI
- Templates expect globals/filters registered in `create_app()` (e.g. `thai_be`, `BE_YEAR`, `CURRENT_USER`, `has_endpoint`). When adding routes, ensure endpoint names match templates (e.g. `import_orders_view`, `dashboard`). Many UI strings are Thai—keep translations consistent.

Integration points & examples
- To compute SLA text in backend: `from utils import sla_text; sla = sla_text(platform, order_time)`.
- To parse spreadsheet time: `from utils import parse_datetime_guess; dt = parse_datetime_guess(value_from_df)`.

Troubleshooting & tips
- If adding DB columns, either implement the same ALTER-IF-MISSING pattern in `app.py` or add clear upgrade steps for maintainers.
- Preserve optional attribute checks (e.g., `product_id`, `product.stock_qty`) used in `importers.py` — some code paths expect those fields to be absent.
- There are currently no automated tests; add focused unit tests for `parse_datetime_guess`, `compute_due_date`, and `import_orders` grouping when changing parsing/allocation logic.

Files to inspect quickly: `app.py`, `importers.py`, `utils.py`, `models.py`, `templates/*.html`, `static/main.js`.

If any part should be expanded (small unit tests, sample spreadsheets, or a migration suggestion), tell me which area and I will add it.
- If you add DB columns, either implement the same ALTER-IF-MISSING pattern used in `app.py` or introduce a proper migration workflow (document the change and update startup logic).
