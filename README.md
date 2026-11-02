# NakamaDesk Backend

The FastAPI server behind **Vriksha Studios** (FurniBiz ERP): one API for the shop's desktop app, the phone app, the public website and the workshop PC. It owns the data (PostgreSQL) and is the only thing that talks to it.

| Client | Repo |
|---|---|
| Desktop POS and back office (PyQt6) | [nakama-desktop](https://github.com/Nakama-Nova/nakama-desktop) |
| Phone app (Flutter) | [vriksha-mobile](https://github.com/Nakama-Nova/vriksha-mobile) |
| Public website (Next.js) | [vriksha-storefront](https://github.com/Nakama-Nova/vriksha-storefront) |
| Workshop worker (photos or video to 3D model and G-code) | [vriksha-scan](https://github.com/Nakama-Nova/vriksha-scan) |
| Old native Android app (retired) | [furnibiz-android](https://github.com/Nakama-Nova/furnibiz-android) |

New here? Read [docs/ONBOARDING.md](docs/ONBOARDING.md) first.

---

## What it does

Routes live in `app/api/`, one file per area, all mounted in `app/main.py`. Open `/docs` (not served in production) for the full list.

- **Shop floor:** items and stock (with an inventory movement ledger), sales and bills (PDF), customers, raw materials, bills of materials, orders, production jobs, purchases, suppliers, categories and brands.
- **People and money:** attendance (phone check-in with a GPS accuracy limit), daily wages, payroll, expenses, reports and dashboard. Roles: `owner`, `manager`, `sales`, `achari`, `worker`, enforced per route.
- **Offline sync:** `POST /sync/push` and `GET /sync/pull` for the desktop's local outbox. Pushes are idempotent, conflicts resolve last-write-wins, stock changes are additive deltas, and deletes travel as tombstones.
- **Public website API:** `/public/*` (catalogue, delivery check, checkout, order tracking) with its own response schemas, so cost fields never leak, and a per-IP rate limit. Shoppers can also keep a customer account (`/account`), signing in with a code sent by email or SMS.
- **Scans, 3D Lab and restoration:** presigned uploads to a private S3-compatible bucket, scan jobs that the workshop worker claims, and the 3D Lab links for furniture parts.
- **Notifications and app updates:** a feed for the desktop and phone, and endpoints that serve the newest desktop installer and phone APK from private GitHub Releases.

Money is `Decimal` / `Numeric(10,2)`. Bills are numbered by the server. The shop is **not GST-registered yet**, so `gst_percent` stays 0 on items; do not show or charge GST until that changes.

## Stack

FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, PostgreSQL, JWT (PyJWT, HS256), ReportLab (bill PDFs), boto3 for object storage. Python 3.11 is what CI and the Docker image use; the code targets 3.10+.

Layering: `api -> services -> repositories (UnitOfWork) -> models`. Routers stay thin; rules live in `app/services/`. Design records are in [docs/adr](docs/adr).

---

## Run it

### With Docker (nothing to install but Docker)

Follow [docs/LOCAL_DEV.md](docs/LOCAL_DEV.md): one `docker compose` command starts the API and Postgres.

### Without Docker

You need Python 3.11 and a PostgreSQL database.

```bash
git clone https://github.com/Nakama-Nova/nakamadesk-backend.git
cd nakamadesk-backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

Create a `.env` (never commit it):

```env
ENV=dev
DATABASE_URL=postgresql://user:password@localhost:5432/furnibiz
SECRET_KEY=any-long-random-string
```

```bash
alembic upgrade head
python -m app.cli create-owner     # first login: /auth/register needs an owner token
uvicorn app.main:app --reload      # http://127.0.0.1:8000/docs
```

### Settings

All settings are environment variables, read in `app/core/config.py` and `app/core/security.py`. Only the first three are needed to start.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres address. Falls back to `DEV_DATABASE_URL` (or `TEST_DATABASE_URL` when `ENV=test`) |
| `SECRET_KEY` | JWT signing key. Outside `dev` and `test` the app **refuses to start** if it is missing, the dev default, or shorter than 32 characters |
| `ENV` | `dev` (default), `test`, or anything else for production. Production also hides `/docs` |
| `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT settings (HS256; 60 minutes unless set) |
| `CORS_ORIGINS` | Website origins allowed to call the API from a browser, comma separated. Empty means none |
| `DELIVERY_PINCODES` | Pincodes the shop delivers to, comma separated. Empty means no restriction |
| `PUBLIC_RATE_LIMIT` | Public calls per IP per minute (default 20) |
| `MEDIA_DIR` | Where public product photos and 3D models are kept on disk (default `media`) |
| `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | Private bucket for scans and 3D files (AWS S3, or Cloudflare R2 via the endpoint). With `S3_BUCKET` empty the upload endpoints answer 503 |
| `SMTP_*`, `EMAIL_FROM`, `SMS_*`, `PAY_UPI_ID`, `PAY_UPI_NAME`, `PAY_PHONE` | Sign-in codes by email or SMS, and the UPI details shown after checkout |
| `GITHUB_RELEASES_TOKEN`, `GITHUB_RELEASES_REPO`, `GITHUB_MOBILE_RELEASES_REPO` | Read-only access to the private releases that the update endpoints serve. Empty means updates are off (503) |
| `GPS_MAX_ACCURACY_M` | Phones with a vaguer GPS fix cannot mark attendance (default 100) |

The comments in `app/core/config.py` are the reference for the full list and defaults.

---

## Test and check

```bash
black --check app tests
ruff check app tests
pytest --ignore=tests/performance
```

**`pytest` wipes the database it points at** (`DROP SCHEMA public`). Use a separate, empty database, and note that `DATABASE_URL` wins over `TEST_DATABASE_URL`:

```bash
ENV=test DATABASE_URL=postgresql://user:password@localhost:5432/furnibiz_test PGTZ=UTC SECRET_KEY=test_secret_key_ci pytest --ignore=tests/performance
```

`PGTZ=UTC` matches CI; without it a few time-based tests fail on a machine in another timezone.

CI (`.github/workflows/backend-ci.yml`) runs black, ruff, `alembic upgrade head`, the tests on Postgres 15, the load tests and a dependency and secret scan (these last two only warn), and builds the Docker stack. Keep Alembic to a single head and make migrations additive, so the running code survives them.

## Project layout

```text
app/
  api/            route handlers, one file per area
  services/       business rules
  repositories/   data access and the UnitOfWork
  models/         SQLAlchemy models
  schemas/        Pydantic request and response models
  core/           settings, security, rate limiting
  cli.py          admin commands (create-owner, finance-sync)
alembic/          database migrations
deploy/           Docker Compose, Caddy, backups, CloudFormation
docs/             onboarding, designs, deployment guides, ADRs, brand tokens
tests/            pytest suite
```

## Documentation

| Doc | What it covers |
|---|---|
| [ONBOARDING.md](docs/ONBOARDING.md) | Start here: architecture, roles, sync, known gaps |
| [LOCAL_DEV.md](docs/LOCAL_DEV.md) | Run the stack locally with Docker |
| [DEPLOY.md](docs/DEPLOY.md), [DEPLOY_AWS.md](docs/DEPLOY_AWS.md) | Deploy on one VM, or on AWS |
| [BETA.md](docs/BETA.md) | The throw-away beta on Vercel and Supabase |
| [SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md), [MICROSERVICES_DESIGN.md](docs/MICROSERVICES_DESIGN.md), [FINANCE_LEDGER_DESIGN.md](docs/FINANCE_LEDGER_DESIGN.md) | Design drafts and decisions. The finance ledger plan is **not built yet** |
| [docs/design](docs/design) | Scan and 3D Lab, restoration, letters, phone screens |
| [SECURITY.md](SECURITY.md) | How to report a vulnerability |

## Licence

Proprietary software. Copyright (c) 2026 Chaithanya, all rights reserved. It may not be copied, modified or shared; see `LICENSE`. Third-party packages keep their own licences.
