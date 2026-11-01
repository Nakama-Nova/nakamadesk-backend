# Vriksha Studios: Microservices Design

**Status:** Draft v1 for review · **Date:** 2026-11-01 · **Scope:** the whole platform at full scale: storefront with customer accounts, staff apps at several locations, inventory, orders, finance, notifications, analytics, and the local AI 3D pipeline.

**This document supersedes** decisions D1 (one monolith), D5 (guest checkout only) and the "one shop, one machine" and "about ₹1,000 a month" answers in [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) §0–1. Those were right for the stage the business was in; the requirements below are not that stage. Everything else in that document (Next.js storefront, `<model-viewer>`, R2 for files, UPI-first payments, GST rules) still holds unless stated here.

Legend: ✅ taken from the repo · ⚠️ assumption, please confirm.

---

## 1. Requirements

### Functional
1. **Customers** create an account on vrikshastudios.com, browse wood carvings and furniture, order (standard and custom), pay, track, and see history.
2. **Staff** log in with roles: owner, manager, sales (counter), staff/worker, achari (master craftsman), artisan (carver).
3. **Three separate inventory masters** ⚠️ (my reading; correct me):
   - **Machines**: a fixed-asset register. Each has a tag, a plant unit, a purchase cost, a maintenance schedule and downtime history. They are not "stock".
   - **Tools**: durable items (chisels, clamps) issued to and returned by a person, with condition and replacement tracking.
   - **Materials and consumables**: wood (by species, grade, cft), adhesive, finish. Quantities move through a ledger, per location.
   - Finished goods (things we sell) are stock too, per location.
   - **Budgets** cover all of the above and live in Finance (§6.6).
4. **Finance** sees everything: online orders, in-store sales, purchases, wages, expenses, machine depreciation. It produces invoices, a ledger, budgets versus actuals, and receivables and payables.
5. **Multiple locations** (shops, plant sites) each run the desktop app. Stock, sales and attendance are per location.
6. **Separate services**: Notification, Order, Finance, Analytics and the others below, each in Docker, tied together.
7. **Local AI model** produces 3D models from photos or scans; the output is mapped to products.
8. Plant units (Manufacturing/Production, Painting/Processing, QC) with GPS-verified attendance ✅ (already built, PR 67).

### Non-functional
| | Target | Why |
|---|---|---|
| Storefront and checkout availability | 99.9% | Lost orders are lost money |
| Staff apps | 99.5% | A workshop can pause for minutes, not hours |
| Finance correctness | **Exactly-once effect** on the ledger, auditable, no silent edits | Tax and trust |
| Recovery | Finance: RPO ≤ 5 min, RTO ≤ 1 h. Others: RPO ≤ 15 min, RTO ≤ 4 h | Realistic for a small team |
| Latency | Storefront page ≤ 300 ms p95 from cache; API p95 ≤ 300 ms | |
| Privacy | Customer PII consent and deletion (India DPDP Act 2023) ⚠️ confirm with counsel | New once customers have accounts |

### Load (my estimate for "full scale" ⚠️)
Staff: 20–100 users, 3–10 locations. Storefront: ~10k visits a day, 50–300 orders a day, 5k products. 3D files: ~30 MB each, so ~150 GB. Peak events: under 20 a second.
**The honest takeaway: load is tiny.** Nothing here needs microservices for throughput. The reasons to split are isolation (a bad deploy of Notification must not stop checkout), different runtimes (GPU/Python for AI), independent release of the storefront, and a clean audit boundary around money. That drives which boundaries are worth paying for.

### Constraints
Existing FastAPI/SQLAlchemy/Postgres code ✅ (about 40 models, 40 services), an existing desktop (PyQt6) and phone app, and a small team ⚠️ (I assume 1–3 engineers). Team size is the dominant constraint on how many deployables you can run well.

---

## 2. Recommendation in one screen

1. **Build the target below, but get there in phases.** A big-bang rewrite into 14 services will stall. Use the strangler pattern: carve services out of the running monolith, one at a time, each with a parity check.
2. **Phase 0 is a modular monolith** with enforced context boundaries, an outbox and Docker images. It is the cheapest step and every later extraction depends on it.
3. **Separate first what has a real reason to be separate**: Notification (failure isolation), Media/3D + AI worker (GPU, Python-ML runtime, runs on your own machine), Analytics (read-only, zero risk), then Identity, Order, Stock, Finance.
4. **Cap the languages at three** (Python, TypeScript, and SQL-driven analytics tooling). "Independent technology per service" is allowed, but each extra language costs hiring, CI, base images and on-call knowledge. Where I pick something different it has to earn it (§4).
5. **Finance is a double-entry ledger** fed by events and reconciled daily against Orders and Payments. It is built correctly once, then not touched casually.
6. **Retire generic offline sync.** Internet is nearly always on, so the desktop becomes online-first with a command outbox and idempotency keys (§8.3). This removes the hardest code in the current repo (LWW over every entity).
7. **Money:** the full stack needs far more than ₹1,000 a month (§12). That is the biggest decision for you, and it is yours. A staged rollout lets cost grow with each phase.

---

## 3. Bounded contexts and services

Each row is a context with its own database. "Phase-1 unit" says which deployable it ships in at first, so you run **6 deployables in phase 1, not 14**; splitting further is mechanical because the boundaries already exist.

| Service | Owns | Tech | Store | Phase-1 unit |
|---|---|---|---|---|
| **gateway** | TLS, routing, rate limits, request ids | Traefik (off the shelf) | none | gateway |
| **identity** | Staff realm and customer realm: credentials, MFA, sessions, social/phone-OTP login | Keycloak (off the shelf) | Postgres | identity |
| **directory** | Locations, plant units, devices (which desktop belongs to which location) | Python/FastAPI | Postgres | `erp-core` |
| **customer** | Customer profiles, addresses, consent, guest→account merge by verified phone | Python/FastAPI | Postgres | `commerce` |
| **catalog** | Products, categories, variants, prices list, publish state, links to media | Python/FastAPI + Meilisearch | Postgres + search + Redis | `commerce` |
| **order** | Sales orders (web, store, custom, phone), quotes, fulfilment, checkout saga | Python/FastAPI | Postgres | `commerce` |
| **payments** | PSP adapters (UPI/Razorpay), webhooks, refunds. Manual UPI recording until a gateway account exists ⚠️ | Python/FastAPI | Postgres | `commerce` |
| **stock** | Materials, consumables, finished goods; per-location ledger; reservations | Python/FastAPI | Postgres | `erp-core` |
| **assets** | Machines (register, maintenance, depreciation inputs) and tools (issue/return) | Python/FastAPI | Postgres | `erp-core` |
| **procurement** | Suppliers, purchase orders, goods receipts | Python/FastAPI | Postgres | `erp-core` |
| **production** | Jobs, BOM, routing through plant units, material allocation, **QC pass/fail** | Python/FastAPI | Postgres | `erp-core` |
| **workforce** | Staff records, attendance (GPS), wages, advances, payroll | Python/FastAPI | Postgres | `erp-core` |
| **finance** | Chart of accounts, journal, invoices (consecutive series), AR/AP, budgets, tax | Python/FastAPI | Postgres (own instance) | `finance` |
| **notification** | Templates, channels (email, SMS, WhatsApp, push), preferences, retries, dead letters | TypeScript (Node) | Postgres + Redis | `notification` |
| **analytics** | Read-only warehouse, dashboards | ClickHouse + dbt + Metabase | ClickHouse | `analytics` |
| **media3d** | Asset registry, scan/AI jobs, candidate matching, `.glb` processing, signed URLs | Python (ML stack) | Postgres + object store | `media3d` |
| **ai-worker** | Runs the local model on your GPU machine | Python, GPU container | local disk | on-prem |

Phase-1 deployables: `commerce`, `erp-core`, `finance`, `notification`, `media3d` (+ `ai-worker` on-prem), plus off-the-shelf gateway, identity and analytics. A "deployable" can hold several contexts as separate packages and separate DB schemas (or databases), which keeps boundaries honest.

**Boundary rules (enforced in CI with `import-linter`)**
- No context imports another's models or tables. No cross-context SQL joins. Reference by id, and copy the few fields you need (an order line keeps the product name and price at the time of sale).
- A context changes another only through its API or by publishing an event.

---

## 4. Technology choices, and why

| Concern | Choice | Reason | Cost of the choice |
|---|---|---|---|
| Core services | **Python 3.12, FastAPI, SQLAlchemy 2, Alembic** | 40 models and 40 services already exist and are tested ✅. Rewriting is the single biggest risk | Python is not the fastest, irrelevant at this load |
| Notification | **TypeScript (Node 22)** | Shares email/WhatsApp template tooling (React Email/MJML) with the storefront, so there are two languages in the whole system, not three | One more runtime to patch |
| Storefront | **Next.js on Vercel** ✅ (D4) | Static pages, SEO, `<model-viewer>` for 3D | Vercel cannot run workers or the broker, so those live in Docker |
| Identity | **Keycloak**, two realms (staff, customers) | Do not write auth. Gives OIDC, MFA, social and OTP login, session revocation. Replaces my hand-rolled `token_version` | Heavy (about 0.7 GB RAM), and a Java component to upgrade |
| Event backbone | **Redpanda** (Kafka API, one binary) | Durable log with replay: Finance and Analytics can rebuild from history | Needs ~1 GB RAM and ops knowledge. Lighter alternative: NATS JetStream; pick this if you stay small |
| Relational | **PostgreSQL 16, one database per service** | Strong consistency inside a context; the team knows it | Several instances to back up (use managed) |
| Cache / rate limits | **Redis 7** | Sessions, catalog cache, rate limits, notification dedupe | |
| Search | **Meilisearch** | Typo-tolerant product search with almost no tuning | Replaceable behind the catalog API |
| Analytics | **ClickHouse + dbt + Metabase** (phase 1: Postgres read replica + Metabase) | Columnar scans for reports; SQL, not services | Do not start with ClickHouse; earn it |
| Files | **S3-compatible**: MinIO on-prem, **Cloudflare R2** in cloud ✅ | Cheap, free egress, signed URLs | |
| Service-to-service | **REST + OpenAPI** (sync), **events** (async) | Debuggable with curl. gRPC only if a hot path proves it | Chatty calls need care |
| Contracts | `contracts/` repo folder: OpenAPI per service, JSON Schema per event (CloudEvents envelope) | Breaking changes fail CI | Discipline |
| Observability | **OpenTelemetry → Grafana (Prometheus, Loki, Tempo)** or Grafana Cloud free tier | One trace id from storefront click to ledger row | |
| Containers | **Docker, one image per deployable**, multi-stage, non-root, healthchecks | | |
| Repo | **Monorepo**: `services/*`, `libs/*`, `contracts/`, `deploy/` | Atomic changes across contracts; CI path filters per service | Needs per-path CI |

---

## 5. Architecture

```
 Customers (web, phone)                  Staff (desktop @ many locations, phone)
        │                                              │
 ┌──────▼───────────┐                           ┌──────▼──────────────┐
 │ Next.js storefront│                           │ PyQt6 desktop /      │
 │ (Vercel) = BFF    │                           │ mobile  (outbox)     │
 └──────┬───────────┘                           └──────┬──────────────┘
        │  OIDC (customer realm)                       │  OIDC (staff realm)
        └───────────────────┬──────────────────────────┘
                     ┌──────▼──────┐        ┌────────────┐
                     │  gateway     │◀──────▶│  identity   │ Keycloak (2 realms)
                     │  (Traefik)   │        └────────────┘
                     └──────┬──────┘
   ┌──────────┬────────────┼─────────────┬─────────────┬────────────┐
   ▼          ▼            ▼             ▼             ▼            ▼
┌────────┐┌────────┐ ┌──────────┐  ┌──────────┐  ┌─────────┐ ┌──────────┐
│commerce││erp-core│ │ finance  │  │ media3d  │  │ notif.  │ │analytics │
│customer││directory│ │ ledger   │  │ assets + │  │ email   │ │ClickHouse│
│catalog ││stock   │ │ invoices │  │ AI jobs  │  │ sms/wa  │ │ Metabase │
│order   ││assets  │ │ budgets  │  └────▲─────┘  └────▲────┘ └────▲─────┘
│payments││procure.│ └────▲─────┘       │ pull jobs    │            │
└───┬────┘│prod.   │      │             │ (outbound)   │            │
    │     │workforce│     │        ┌────┴──────┐       │            │
    │     └───┬────┘      │        │ ai-worker │       │            │
    │         │           │        │ (GPU, on- │       │            │
    ▼         ▼           ▼        │  prem)    │       │            │
 Postgres  Postgres   Postgres     └───────────┘       │            │
 (per ctx) (per ctx)  (own inst)                       │            │
    └─────────┴───────────┴──────── events (outbox → Redpanda) ─────┴────────────┘

 Object store (R2 / MinIO): images, .glb, scans, invoice PDFs          Redis · Meilisearch
 Observability: OpenTelemetry → Prometheus / Loki / Tempo → Grafana
```

**Why the Next.js app is the storefront BFF:** it already runs server-side; it aggregates catalog, stock-availability and customer data into page-shaped responses. A separate BFF service would add a deploy without a reason.

---

## 6. Deep dive

### 6.1 Communication

- **Synchronous (REST)** only when the caller needs the answer now: "reserve this stock", "what is the price".
- **Asynchronous (events)** for everything that merely reacts: Finance, Notification, Analytics, search indexing.
- **Transactional outbox** in every service: the business row and its event row commit in one DB transaction; a relay publishes to Redpanda. This avoids the classic dual-write bug (row saved, event lost).
- **Idempotent consumers**: each consumer keeps `processed_events(event_id)`. Every command that creates something takes a client-generated `Idempotency-Key` (the same idea as `SyncLog.client_id` ✅).
- **Event envelope** (CloudEvents): `id, type, source, time, subject, dataschema, data`. Event types are versioned (`order.confirmed.v1`). Additive changes only; a breaking change is a new type published alongside the old one until consumers move.

Core events:

| Event | Producer | Consumers |
|---|---|---|
| `order.placed / confirmed / delivered / cancelled` | order | finance, notification, analytics, production |
| `payment.captured / refunded` | payments | order, finance, notification |
| `stock.reserved / released / committed / adjusted / transferred` | stock | order, finance (COGS), analytics |
| `procurement.goods_received / bill_posted` | procurement | stock, finance (AP) |
| `production.job_started / qc_passed / qc_failed / completed` | production | order, stock, notification, analytics |
| `workforce.attendance_marked / wage_paid` | workforce | finance, analytics |
| `asset.maintenance_due / depreciation_run` | assets | notification, finance |
| `media.asset_generated / asset_linked` | media3d | catalog, notification |
| `catalog.product_published` | catalog | search index, storefront revalidation |

### 6.2 Online checkout saga (order service orchestrates)

```
Customer      Storefront      Order          Stock        Payments       Finance     Notification
   │ place order  │             │              │             │              │             │
   │─────────────▶│  POST /orders (Idempotency-Key)           │              │             │
   │              │────────────▶│ status=pending_payment      │              │             │
   │              │             │ reserve(lines, loc, ttl 30m)│              │             │
   │              │             │─────────────▶ ok / short    │              │             │
   │              │             │ create_intent(amount)       │              │             │
   │              │             │─────────────────────────────▶              │             │
   │◀─ pay (UPI/PSP) ──────────────────────────────────────────             │             │
   │              │             │              │   webhook → payment.captured│             │
   │              │             │◀─────────────────────────────              │             │
   │              │             │ commit reservation ─▶        │              │             │
   │              │             │ status=confirmed → order.confirmed ───────▶│ AR, invoice │
   │              │             │ ─────────────────────────────────────────────────────────▶ email/WA
   │ failure or timeout: Order releases the reservation and cancels (compensation)
```
- Reservations expire (TTL) so a crashed saga cannot lock stock forever.
- Payments is the only service that talks to the PSP; webhooks are verified (HMAC) and deduplicated by provider reference ✅ (same pattern as the existing design).
- Until a payment gateway exists ⚠️, "payment captured" is a staff action (they mark the UPI advance received), and the same event is published. Nothing downstream changes when a gateway is added later.
- Made-to-order lines create a `production` job on `order.confirmed` instead of committing stock.

### 6.3 In-store sale (desktop, any location)
`POST /orders` with `channel=store`, `location_id`, and an idempotency key made on the device. The sale is paid at the counter, so the order is confirmed immediately. Stock is committed with `allow_negative=true`, as today ✅ (the goods have already left), and a negative balance raises an alert for a manager. If the network blips, the desktop's outbox replays the same command safely.

### 6.4 Inventory models (three masters, one ledger style)

**Stock (materials, consumables, finished goods)**
- `stock_items(id, kind, name, unit, species/grade, …)` and `locations`.
- `stock_movements(id, item_id, location_id, qty_delta, reason, ref_type, ref_id, unit_cost, at, by)`. **Append-only.** The balance per `(item, location)` is a projection kept in a table and rebuilt from the ledger on demand.
- `reservations(id, item_id, location_id, qty, order_id, expires_at, state)`.
- Transfers between locations are two movements linked by a `transfer_id`, with an *in transit* state.
- Valuation: weighted average cost, stored per movement so Finance can compute COGS.

**Assets (machines and tools)**
- `machines(tag, plant_unit_id, purchase_date, cost, useful_life, method, status)`, `maintenance_plans(machine_id, every_days | every_hours)`, `maintenance_log`, `downtime_log`.
- `tools(tag, kind, condition, status)`, `tool_issues(tool_id, user_id, issued_at, returned_at, condition_out/in)`. A lost or broken tool becomes a Finance expense event.

**Budgets** are in Finance, never in stock: `budget_lines(period, cost_center, category, amount)`. Spend is computed from events (purchases, consumption, depreciation, wages), so a budget cannot drift from reality.

### 6.5 Catalog and 3D linking

- **Catalog** owns *what we sell* (name, copy, variants, price, publish state). **Stock** owns *how many*. **media3d** owns files and their processing. Catalog stores only `product_assets(product_id, asset_id, kind, position)`.
- **AI flow** (the model stays on your machine):
  1. A scan job is created in `media3d`, normally *from a product or production job*, so the product is known up front. This is the reliable path.
  2. `ai-worker` (GPU box in the workshop) **pulls** jobs over outbound HTTPS or a private network (Tailscale/WireGuard). Nothing in the workshop is exposed to the internet.
  3. It downloads inputs by signed URL, runs the model, uploads `.glb` plus a preview, and reports `media.asset_generated` with quality metrics.
  4. For jobs with no product attached, `media3d` stores an embedding and proposes **candidate products** (nearest by image/text embedding).
  5. **A manager confirms the link.** The system never auto-publishes. On confirm, `product_assets` is written and `catalog.product_published` triggers storefront revalidation.
- Assets are immutable and versioned; "replace model" creates a new version, and the old one stays for rollback.

### 6.6 Finance

- **Double-entry journal**: `accounts`, `journal_entries(id, posted_at, source_event_id, memo)`, `journal_lines(entry_id, account_id, debit, credit)`. A DB constraint forces debits = credits per entry. **Entries are never updated or deleted; a mistake is fixed by a reversing entry.**
- **Posting rules** (one small function per event type): `order.confirmed` → Dr Receivable, Cr Sales (and Cr Tax payable once GST-registered ⚠️); `payment.captured` → Dr Bank/UPI, Cr Receivable; `stock.committed` → Dr COGS, Cr Inventory; `procurement.bill_posted` → Dr Inventory/Expense, Cr Payable; `workforce.wage_paid` → Dr Wages, Cr Cash; monthly depreciation job → Dr Depreciation, Cr Accumulated depreciation.
- **Invoices**: one consecutive series per financial year, issued under a row lock so numbers have no gaps ✅ (the existing `invoice_counters` approach). Plain "Bill" until GST registration ✅.
- **Daily reconciliation job**: Finance compares its totals with Orders and Payments for the day and raises an alert on any difference. This catches lost events, which is what ruins ledgers.
- **Rebuild**: because Finance consumes a replayable log, the ledger can be reconstructed from events in a test environment to verify the posting rules.
- Money is `Decimal`/`NUMERIC(14,2)` everywhere, never float ✅.

### 6.7 Analytics
Consumes all events into ClickHouse tables (`fact_order_line`, `fact_stock_movement`, `fact_attendance`, `fact_journal_line`) with dbt models for dashboards: revenue by channel and location, margin by product, stock cover, unit throughput, wage cost per unit, QC first-pass yield, budget versus actual. It is read-only and can be wrong or down without affecting any business flow.

### 6.8 Notification
Subscribes to events, renders a template per channel and locale, respects customer and staff preferences, dedupes on `(event_id, recipient, channel)`, retries with backoff, and moves permanently failing messages to a dead-letter topic. Channels sit behind one interface (email, SMS, WhatsApp, push). It holds no business rules: "tell the customer their order is ready" is a template bound to `order.ready`.

---

## 7. Identity and security

- **Two Keycloak realms.** Staff: roles `owner, manager, sales, staff, achari, artisan`, MFA required for owner and manager, `loc` claim lists the allowed locations. Customers: phone OTP, email, and Google; no staff roles can exist in that realm.
- Each service **validates the JWT itself** (JWKS) and checks role and location claims. The gateway handles routing and rate limits but is not the only gate.
- **Service-to-service**: short-lived client-credentials tokens, scoped per service. Internal network only. mTLS can come later behind a service mesh if needed.
- **Roles map to permissions** inside each service (`stock:adjust`, `finance:post`, `attendance:mark`), so changing who can do what is a config change.
- **Customer PII** is confined to `customer`, `order` (address snapshot) and `notification`. Deletion requests are an event (`customer.erasure_requested`) that each holder honours or anonymises.
- **Secrets** come from the platform secret store, never `.env` in git ✅ (existing rule). Images are scanned in CI; dependencies pinned and audited ✅ (existing `pip-audit`/`gitleaks` jobs).
- The existing hardening carries over: server-decided wages, sessions ended on credential change, audit trail on account changes ✅. In the new model, audit events go to the log and to a per-service `audit` table.

---

## 8. Multi-location and the desktop

### 8.1 Locations and devices
`directory` owns `locations(id, name, kind: shop|plant|warehouse, address, geo)`, `plant_units(location_id, …)` ✅, and `devices(id, location_id, enrolled_by)`. A manager enrols a desktop once; its token carries `device_id` and `loc`. Every write carries `X-Location-Id`, which the service checks against the token.

### 8.2 What is per location
Stock balances, sales, attendance, cash drawers, and budgets (optional). Catalog, customers and suppliers are global.

### 8.3 Replace LWW sync with commands + outbox
Because the internet is almost always available:
- The desktop and phone call the same APIs as everything else, **online-first**.
- Writes go through a local SQLite **outbox of commands** (not row snapshots), each with an `Idempotency-Key`. If the network drops, commands queue and replay in order. The server applies domain rules, not last-write-wins.
- Reads for the counter (catalog, prices, stock) are cached locally with a short TTL so a brief outage does not stop a sale.
- Conflicts become explicit business cases (two sales of the last chair → stock goes negative and alerts), not silent overwrites.
- **Migration:** keep the current `/sync` endpoints during the transition and advertise `sync_protocol_version` ✅ (ADR-0002) so older desktops keep working until updated.

---

## 9. Docker and deployment

### 9.1 Layout
```
repo/
  services/{commerce,erp-core,finance,notification,media3d,ai-worker}/   Dockerfile each
  libs/{auth,outbox,events,observability}/        shared Python/TS libraries (small, versioned)
  contracts/{openapi,events}/                     the source of truth for APIs and events
  deploy/{compose,k8s,traefik,keycloak,grafana}/
```

### 9.2 Compose (single host, phase 1–3)
```yaml
# deploy/compose/docker-compose.yml (abridged, illustrative)
services:
  traefik:      { image: traefik:v3, ports: ["443:443"], networks: [edge] }
  keycloak:     { image: quay.io/keycloak/keycloak, depends_on: [pg-identity], networks: [edge, internal] }
  commerce:     { build: ../../services/commerce, depends_on: [pg-commerce, redpanda, redis], networks: [edge, internal],
                  healthcheck: { test: ["CMD", "curl", "-f", "localhost:8000/health"] }, deploy: { resources: { limits: { memory: 512M } } } }
  erp-core:     { build: ../../services/erp-core, depends_on: [pg-erp, redpanda], networks: [edge, internal] }
  finance:      { build: ../../services/finance, depends_on: [pg-finance, redpanda], networks: [internal] }   # not on the edge network
  notification: { build: ../../services/notification, depends_on: [redis, redpanda], networks: [internal] }
  media3d:      { build: ../../services/media3d, depends_on: [pg-media, redpanda], networks: [edge, internal] }
  redpanda:     { image: redpandadata/redpanda, networks: [internal] }
  redis:        { image: redis:7, networks: [internal] }
  meilisearch:  { image: getmeili/meilisearch, networks: [internal] }
  minio:        { image: minio/minio, profiles: [dev], networks: [internal] }   # R2 in cloud
  pg-commerce:  { image: postgres:16, volumes: [pgc:/var/lib/postgresql/data], networks: [internal] }
  # pg-erp, pg-finance, pg-media, pg-identity likewise; in cloud these are managed instances
  analytics:    { image: clickhouse/clickhouse-server, profiles: [analytics], networks: [internal] }
  metabase:     { image: metabase/metabase, profiles: [analytics], networks: [edge, internal] }
networks: { edge: {}, internal: { internal: true } }
```
Rules: only Traefik and the storefront-facing APIs join `edge`; **databases and Finance never do**. Each service has a healthcheck, memory limit, non-root user and read-only filesystem where possible. Migrations run as a one-shot job *before* a new version starts, and must be additive (expand, then contract) ✅ (the rule this repo already follows).

### 9.3 Where it runs
| Stage | Setup | Notes |
|---|---|---|
| Dev | `docker compose up` with profiles | Same images as production |
| Phase 1–3 | **One or two VMs** (Mumbai/Bangalore) running compose, **managed Postgres**, R2, Vercel for the storefront | Cheapest honest setup. Backups and failover come from the managed DB |
| Later | Cloud Run / ECS Fargate or Kubernetes | Only when you need autoscaling or multi-host rollouts. Compose files translate directly |
| On-prem | `ai-worker` on the GPU machine, outbound-only | Cloud never calls in |

**Note:** Vercel cannot host the broker, workers or Keycloak, so the API side moves off Vercel's serverless functions into containers. The storefront stays on Vercel.

### 9.4 CI/CD
Per-service pipeline on path changes: black/ruff or eslint, unit tests, **contract tests** (provider checks its OpenAPI and event schemas; consumers check theirs), build image, scan, push. Integration environment runs Postgres + Redpanda in compose. Deploy: staging automatically, production behind a manual gate; rollback is the previous image tag. Database migrations run as a job and are tested by upgrading from a snapshot of production data shape.

---

## 10. Reliability and operations

- **SLOs and alerts**: checkout success rate, p95 latency per service, outbox lag (events waiting to publish), consumer lag per topic, dead-letter count, reconciliation difference, stock-negative alerts, disk and backup freshness.
- **Failure behaviour**
  - Notification down → customers get messages late; no order is lost.
  - Analytics down → dashboards stale; nothing else affected.
  - Finance down → events queue in the log and post on recovery; invoices wait but sales continue.
  - Stock down → the storefront shows "check availability"; staff keep a degraded cached view. Checkout needs reservations, so online ordering pauses (the one hard dependency).
  - Broker down → services keep committing to their outbox tables and catch up later.
- **Resilience patterns**: timeouts on every call, bounded retries with jitter, circuit breaker on the Stock and Payments calls from Order, bulkheads (separate worker pools), idempotent handlers everywhere.
- **Backups**: managed Postgres point-in-time recovery; weekly restore test; object store versioning; Redpanda topics retained 7–30 days (Finance can also rebuild from its own journal).
- **Observability**: every request and event carries a `trace_id`; structured JSON logs; dashboards per service; one on-call runbook per alert.

---

## 11. Trade-off analysis

| Decision | Gain | Cost | Verdict |
|---|---|---|---|
| Microservices instead of the monolith | Isolation, independent releases, clear audit boundary around money, GPU/ML runtime separation | 5–10× operational surface; distributed-systems bugs (lost events, partial failures); slower feature work across contexts; needs observability from day one | **Yes, but staged.** Not justified by load; justified by isolation and by where the business is going |
| 14 services vs 6 deployables first | Same boundaries, a third of the on-call burden | Larger deployables | **6 first.** Split when a team or release cadence demands it |
| Database per service | True independence | No joins, eventual consistency, more backups | Yes, but sharing one Postgres *instance* with separate databases is acceptable in phase 1 |
| Events + outbox | Loose coupling, replayable history | Extra table and relay; eventual consistency | Yes |
| Orchestrated saga for checkout | Clear owner of the flow, easy to debug | Order knows the steps | Yes. Choreography becomes unreadable past three steps |
| Redpanda vs RabbitMQ vs NATS | Redpanda: replay and an analytics ecosystem | More memory and ops than RabbitMQ/NATS | Redpanda; NATS JetStream if cost matters more than replay |
| Keycloak vs custom auth | No auth code to maintain; MFA and OTP included | Heavy; another component | Keycloak. Auth is the worst place to be clever |
| Double-entry ledger vs "totals in tables" | Correct, auditable, reconstructable | More upfront modelling | Yes. Retrofitting later is far worse |
| Three languages vs "best tool per service" | Hiring, CI and image sprawl stay manageable | Occasionally a less-than-ideal tool | Cap at Python + TypeScript + SQL tools |
| Online-first + command outbox vs full offline replication | Far simpler, no conflict resolution engine | A longer outage degrades the counter | Yes, given the network you described |
| REST vs gRPC internally | Easy to debug, universal | Slightly bigger payloads | REST now |
| Reuse the Python code vs rewrite | Weeks, not months; proven rules | Inherits some legacy | Reuse, behind new boundaries |

---

## 12. Cost, honestly ⚠️ (order of magnitude; check current prices)

| Stage | What runs | Rough monthly |
|---|---|---|
| Today | 1 small VPS, Postgres on it | ₹700–900 (the earlier budget) |
| Phase 0–1 | 1 VM (4 vCPU / 8 GB), managed small Postgres, R2 | ₹4,000–8,000 |
| Phase 2–4 | 1–2 VMs (8 vCPU / 16 GB), managed Postgres ×2, Redpanda, Keycloak, Grafana Cloud free | ₹10,000–20,000 |
| Kubernetes / HA | multi-node, managed everything | ₹40,000+ |

Ideas to lower it: put all non-Finance databases in one managed Postgres instance as separate databases; use NATS instead of Redpanda; skip ClickHouse until Postgres reports get slow; use Grafana Cloud's free tier.

---

## 13. Migration plan (strangler fig)

Each step ends with the old code deleted, not left running in parallel.

| Phase | Work | Size | Exit criteria |
|---|---|---|---|
| **0. Modular monolith** | Package per context; `import-linter` forbids cross-imports; add outbox table and event publisher; contracts folder; Dockerfile and compose for the monolith | M | CI fails on a cross-context import; every state change emits its event |
| **1. Platform** | Gateway, Keycloak (staff realm; migrate users), Redpanda, Redis, observability, managed Postgres, CI per service | M–L | A request is traceable from gateway to DB; staff log in through Keycloak |
| **2. Edge services** | **Notification** (replaces `notification_service.py`), **Analytics** (read-only), **media3d + ai-worker** (replaces `lab`/`scans`) | M | Each runs in its own container; the monolith no longer imports them |
| **3. Customer-facing** | Customer realm and accounts, `customer`, `catalog` (+ search), storefront on the new APIs; `payments` when a gateway account exists | L | Customers can register, order and track; guest checkout still works |
| **4. Transactional core** | **stock + assets + procurement**, then **order** (saga), then **finance** run in *shadow mode* beside the old tables for a full month and reconcile to the paisa before cutover | L–XL | Finance totals equal the old reports; the old tables are retired |
| **5. Optional splits** | Production, workforce, directory become separate deployables if there is a reason | M | |

Parity technique for each extraction: dual-write or event-replay into the new service, compare read results, cut traffic over by route in the gateway, keep a rollback switch for one release, then delete.

---

## 14. Risks and open questions

**Risks**
1. **Over-building for the team.** Mitigation: phase gates, 6 deployables, stop when the next split has no clear reason.
2. **Lost or duplicated events corrupt Finance.** Mitigation: outbox, idempotent consumers, daily reconciliation.
3. **Cost outruns revenue.** Mitigation: managed services only where they save real ops time; decide the budget (below).
4. **Data migration.** Moving live money data is the riskiest step; shadow mode is mandatory.
5. **Keycloak and Redpanda are new skills.** Plan time for learning, or pick the lighter options.

**Questions for you** (these change the design)
1. **Budget:** is the ₹1,000 a month limit gone? §12 shows what each stage costs.
2. **Team:** how many engineers, now and in six months? This sets how many deployables you can run.
3. **Inventory masters:** is my reading of Machines / Tools / Materials in §1 right? Are budgets per location, per plant unit, or per category?
4. **Locations:** how many shops and plants, and does each keep its own stock and cash?
5. **Payments:** which gateway (Razorpay, PhonePe, Cashfree)? Do you have a business registration for it yet?
6. **GST:** registered yet? This decides when tax lines appear on invoices.
7. **Delivery and returns:** still local delivery only? Is there a returns and refund policy?
8. **AI model:** what are its inputs (photos, video, scans) and typical output size and time? Which GPU machine?
9. **Data protection:** do you need legal review for customer accounts under the DPDP Act?
10. **Hosting:** which cloud or provider do you prefer, and must data stay in India?

## 15. What I'd revisit as it grows
- Split `erp-core` and `commerce` further once separate teams or release cadences exist.
- Move to Kubernetes when you need autoscaling or zero-downtime rollouts on multiple hosts.
- Add a data-contract layer (schema registry) when more than two teams publish events.
- Introduce gRPC for any internal call over ~50 requests per second.
- Add a service mesh (mTLS, retries) only when the number of services makes per-service libraries a burden.
- Revisit multi-tenancy (ADR-0001) if this platform is ever sold to other workshops: it is easier to design in `tenant_id` at the context boundary now than to retrofit later.
