# NAKAMA / FurniBiz: New Developer Onboarding

Last verified: 2026-10-23, against `origin/main` of each repo (backend `e9b9f04`, desktop `8af46f9`, android `b9036eb`).

## 1. What this is

An ERP for a furniture manufacturing and retail business (FurniBiz, brand "Nakama"). It covers inventory, sales and invoicing, customers, raw materials and bills of materials (BOM), orders, production jobs, purchases, and workforce (attendance and daily wages). It is designed **offline-first**: clients keep working without a network and sync later.

GitHub org: `Nakama-Nova`. Three repos:

| Repo | Stack | Role | State |
|---|---|---|---|
| `nakamadesk-backend` | FastAPI, SQLAlchemy 2, PostgreSQL, Alembic | System of record and sync server | Most complete. CI green, 0 open issues |
| `nakama-desktop` | PyQt6, requests, SQLite outbox | Main staff client | Online use works. Counter sales can be saved offline and sync later. Other writes still need a connection. No tests, no CI |
| `furnibiz-android` | Kotlin 2.0, Compose, Retrofit | Mobile client | Skeleton: login and dashboard only. No CI |

## 2. Architecture

```
Desktop (PyQt6) ──┐                     ┌─ PostgreSQL
Android (Compose) ┼── HTTPS/JSON ── FastAPI ── services ── repositories (UnitOfWork) ── models
                  │                     └─ JWT auth + check_role RBAC
Local SQLite outbox (desktop) ── POST /sync/push, GET /sync/pull
```

Backend layering is strict: `api → services → repositories → models`. Routers stay thin, business rules live in `app/services/`, and all DB access goes through the `UnitOfWork` in `app/repositories/sqlalchemy_repo.py`. Guiding principles (from the project's master prompt): modular monolith, domain-driven design, financial correctness, inventory traceability, offline-first, no premature infrastructure.

### Key rules to respect

- **Money** is `Numeric(10,2)` in the DB and `Decimal` in Python. Never use `float`.
- **Inventory** is a ledger: every stock change writes an `InventoryMovement` (`raw_in`, `raw_consumed`, `finished_in`, `finished_out`, `adjustment`). `Item` uses optimistic locking (`version_id`). Sales retry up to 5 times with exponential backoff on conflicts.
- **Bill numbers**: `NTD-YYYY-NNNNN`, consecutive per year from the `invoice_counters` table, no gaps (a rolled-back sale gives its number back). Items keep `gst_percent = 0` until the shop is GST-registered; zero-tax sales print a plain "Bill" with no CGST/SGST.
- **Order status** transitions are enforced: draft → confirmed → in_production → ready → delivered; `cancelled` is reachable from any non-terminal state. `delivered` and `cancelled` are terminal.
- **Single tenant per deployment** (ADR-0001). Do not add `tenant_id` columns.
- **Additive-only API evolution** (ADR-0002). No URL versioning yet; never remove or rename fields.

## 3. Roles and permissions (`UserRole`)

`owner`, `manager`, `sales`, `achari` (master craftsman), `worker`. Enforced by the `check_role([...])` dependency.

| Area | Access |
|---|---|
| Auth: `/auth/me` | any authenticated user |
| Auth: `GET /auth/` (list users) | owner, manager, sales |
| Items CRUD, `PATCH /items/{id}/stock` | owner, manager (stock patch); see router for reads |
| Sales (`/sales`) | owner, manager, sales |
| Customers | CRUD for owner, manager, sales; delete for owner, manager only |
| Invoices and PDF | owner, manager, sales |
| Reports (sales, top-products, inventory, profit-loss, gst-summary, export) | owner, manager |
| Dashboard summary | owner, manager |
| Raw materials | create and list: owner, manager; delete: owner only |
| BOM | create and delete: owner, achari; read: owner, manager, achari |
| Attendance | create, check-in, check-out, list all: owner, manager, sales; `GET /my`: any; `PATCH`: owner, manager |
| Wages | `GET /pending`: own wages, or all for owner and manager; `POST /pay`: owner, manager |
| Orders | create, list, get: owner, manager, sales; status change: owner, manager |
| Production jobs | create, start, complete: owner, manager; list and get: owner, manager, achari |
| Purchases | create and confirm: owner, manager; list and get: owner, manager, sales |
| Sync (`/sync/push`, `/sync/pull`) | any authenticated user |

## 4. Offline sync design

- **Push** `POST /sync/push`: a batch (max 500) of `{id, entity, action, payload, updated_at}`. `id` is the client's operation UUID and is stored as `SyncLog.client_id` (unique), which makes pushes **idempotent**: replaying a batch is safe.
- Each operation runs in its own **SAVEPOINT** (`begin_nested`), so one bad operation fails alone and the rest commit. Response is `{success: [...], failed: [...]}`. Unexpected errors are logged server-side and returned as a generic message.
- Entities with handlers (`SYNC_HANDLER_REGISTRY`): `sale`, `item`, `attendance`, `raw_material`.
- **Conflicts**: last-write-wins by `updated_at` (`LWWResolver`). Stock on `item` and `raw_material` uses `StockDeltaResolver`: the payload value is an additive **delta**, not an absolute value.
- **Pull** `GET /sync/pull?last_sync=...&limit=500`: returns changed rows ordered by `(updated_at, id)` plus a `deleted` list of tombstones (`{entity, record_id, deleted_at}`) from the `sync_tombstones` table. Deleting a synced model through the repository writes a tombstone automatically.
- **Desktop side**: `services/sync_service.py` runs on a 60 s timer from `MainWindow`, pushes the SQLite outbox in batches of 200, and pulls from epoch for a full sync.

## 5. Running it locally

### Backend

```bash
cd nakamadesk-backend
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt -r requirements-dev.txt
# create .env (see below)
alembic upgrade head
python -m app.cli create-owner      # first login; /auth/register needs an owner token
uvicorn app.main:app --reload       # http://127.0.0.1:8000/docs
pytest                              # needs a live PostgreSQL
```

Env vars: `ENV` (default `dev`), `DATABASE_URL`, `DEV_DATABASE_URL`, `TEST_DATABASE_URL`, `SECRET_KEY`, `ALGORITHM` (HS256), `ACCESS_TOKEN_EXPIRE_MINUTES` (default 60). Website API: `MEDIA_DIR` (uploaded photos/models, default `media`), `DELIVERY_PINCODES` (comma separated; empty = no restriction), `CORS_ORIGINS` (storefront origins, comma separated; empty = none), `PUBLIC_RATE_LIMIT` (calls per IP per minute, default 20). Private object store (scans, STL/OBJ/G-code, part models): `S3_BUCKET`, `S3_ENDPOINT_URL` (empty for AWS S3; the account URL for Cloudflare R2), `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`. With `S3_BUCKET` empty the upload endpoints answer 503. Keep the bucket **private** and give the key access to that one bucket only. Suggested lifecycle rule: delete `scans/` source files 60 days after the job is done (keeps storage cost flat). Outside `dev`/`test`, startup **refuses to run** with a missing or default `SECRET_KEY`. `conftest.py` forces `ENV=test`; fixtures `auth_client`, `manager_client`, `worker_client` give pre-authenticated clients. **`pytest` wipes the database it points at** (`DROP SCHEMA public`), and `DATABASE_URL` from `.env` wins over `TEST_DATABASE_URL`: run it with `DATABASE_URL` set to a separate test database, never your dev one. Alembic has a single head (`d0e1f2a3b4c5`); keep it linear.

### 3D Lab (furniture parts)

Mark a piece as furniture, set its overall size and Small/Medium/Large presets, then enter its parts as formulas of `L W H` in the editor. Owner/manager: `POST /lab/links {"item_id": ..., "mode": "edit"}` returns a link to the editor; everyone in the workshop (owner, manager, achari, worker) gets a read-only viewer link with `mode: "view"`. Links last 10 minutes, open the page in a browser, and **cannot be used as a login**. The desktop app has a "3D Lab" screen that opens them. Design: `docs/design/scan-and-lab.md`.

### Desktop

```bash
cd nakama-desktop
pip install -r requirements.txt      # file is UTF-16LE; see gotchas
echo API_BASE_URL=http://127.0.0.1:8000 > .env
python main.py                       # backend must be running
```

Token and role are kept in the OS credential store via `keyring`; the JWT `exp` is checked on restore. Local data is `data/nakama_local.db`.

### Android

Open in Android Studio (JDK 17). Package `com.nakamafurnibiz`, compileSdk and targetSdk 36, minSdk 26, AGP 9.0.1. **Edit `RetrofitClient.BASE_URL`** (currently a hardcoded LAN IP, `10.137.184.42:8000`) to your backend; use `10.0.2.2:8000` for the emulator.

## 6. Tooling and CI

- Format: **black**, 88 columns, `py310` target. Lint: **ruff** (rules E, F, B, I; ignores E501 and B008). Pre-commit runs both.
- Backend CI (`.github/workflows/backend-ci.yml`): `black --check`, `ruff`, `alembic upgrade head`, `pytest` with coverage, on Postgres 15 and Python 3.11. Run `black . && ruff check --fix .` before pushing; CI failures on formatting are the most common mistake.
- Desktop: `.flake8` (max line 127), but no tests and no CI.
- Android: template test stubs only, no CI.
- The backend suite has about 25 test files plus `tests/performance/`. `test_stock_race_condition` is concurrency-sensitive and was loosened to assert invariants (`final_stock == 10 - success_count`) instead of exact counts.

## 7. Known gaps (read before you plan work)

**Security**
1. ~~`POST /auth/register` was open~~ **Fixed:** it now needs an owner token. The first owner is created with `python -m app.cli create-owner`.
2. Android: `usesCleartextTraffic="true"`, `allowBackup="true"`, hardcoded LAN IP.
3. JWT is a single 60-minute access token with no refresh or revocation.

**Functional**
4. **Desktop #4 (partly done)**: counter sales made offline are queued in the outbox and pushed by the background sync (no printable bill until then, because bill numbers come from the server). Still open: other offline writes, a local read cache, and applying pulled data and tombstones on the desktop.
5. ~~Sync-created sales skipped the sale flow~~ **Fixed:** `SaleSyncHandler` now calls `sales_service.build_sale`, so a pushed sale moves stock, writes the ledger entry and gets the next bill number. Stock may go negative for a pushed sale (the goods already left the shop); server prices win over the client price.
6. ADR-0002 follow-ups not done: add `sync_protocol_version` to push and pull responses, with a desktop warning on mismatch. A one-line "single tenant per deployment" note is also missing from the README.
7. Android implements only `POST auth/login` and `GET dashboard/summary`.

**Repo hygiene**
8. `requirements.txt` in the backend pins `black==26.3.1` but `requirements-dev.txt` pins `black==24.2.0`, and CI installs both (dev wins). Align them.
9. Desktop `requirements.txt` is **UTF-16LE with BOM**. Git treats it as binary, some tools choke on it. Convert to UTF-8.
10. Untracked `CHANGELOG.md` in all three repos is stale. The android one wrongly says the repo was never pushed to GitHub (it is on GitHub). Fix or delete before committing.
11. Backend tests need a live Postgres; there is no Docker setup in the repo, so local test runs depend on your own database.

## 8. Decision records

- `docs/adr/0001-multi-tenancy-strategy.md`: single tenant per deployment; revisit only if the business sells this as SaaS.
- `docs/adr/0002-api-versioning-strategy.md`: no `/v1` prefix yet; additive-only schema changes; add `sync_protocol_version` so old clients can detect incompatibility.

## 9. Working conventions

- Track work in GitHub Issues per repo. Small, focused commits; one concern per commit.
- When you change a model, add an Alembic migration and make sure `alembic upgrade head` works on an empty DB.
- Anything that touches money, stock, or sync needs a test. Sync changes need an idempotency check (replay the same batch twice).
- Keep routers thin; put rules in services; use the `UnitOfWork`, not raw sessions, in services.
- Don't commit `.env`, the SQLite DB, or secrets.

## 10. Suggested first week

1. Run the backend, open `/docs`, register and log in, create an item and a sale.
2. Read `app/services/sync_service.py` and `tests/test_sync*.py` end to end.
3. Run the desktop app against your local backend.
4. Pick up the sync follow-ups in gap #6 as a warm-up.
5. Then the rest of desktop #4 (local read cache), the largest remaining feature.

## Desktop app updates

The desktop installers are GitHub Releases of the **private** `nakama-desktop` repo. The app cannot read a private repo, so it asks this server (`GET /app/updates/latest`, then `/app/updates/download/{version}`), which reads the releases with a read-only token and only answers signed-in staff.

Set up once (the token is a secret: enter it yourself, never in git or chat):
1. GitHub, Settings, Developer settings, **Fine-grained personal access tokens**, Generate new token. Resource owner: Nakama-Nova. Repository access: **only** `nakama-desktop`. Permissions: **Contents: Read-only** (Metadata is added automatically). Choose the longest expiry; GitHub caps it at one year.
2. Put it in the server settings as `GITHUB_RELEASES_TOKEN` (on Vercel: project, Settings, Environment Variables, or `vercel env add GITHUB_RELEASES_TOKEN production --sensitive`), then redeploy. `GITHUB_RELEASES_REPO` defaults to `Nakama-Nova/nakama-desktop`.
3. When the token expires the endpoint answers 502 "The update token was refused": make a new one and replace the setting.

Without the token the endpoints answer 503 "Updates are not set up" and the app simply shows no update.
