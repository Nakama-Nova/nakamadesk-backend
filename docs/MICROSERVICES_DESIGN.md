# Vriksha Studios: Microservices Design

**Status:** Draft v4 for review (updated with the owner's answers, §0) · **Date:** 2026-11-01 · **Scope:** the whole platform at full scale: storefront with customer accounts, staff apps at several locations, inventory, orders, finance, notifications, analytics, and the local AI 3D pipeline.

**This document supersedes** decisions D1 (one monolith), D5 (guest checkout only) and the "one shop, one machine" and "about ₹1,000 a month" answers in [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) §0–1. Those were right for the stage the business was in; the requirements below are not that stage. Everything else in that document (Next.js storefront, `<model-viewer>`, R2 for files, UPI-first payments, GST rules) still holds unless stated here.

Legend: ✅ taken from the repo · ⚠️ assumption, please confirm.

---

## 0. Owner answers (2026-11-01): these override anything below that conflicts

| Fact | Consequence |
|---|---|
| **Budget about ₹5,000 a month.** | One VM, no managed database, no Kubernetes. The infrastructure choices in §4, §9.3 and §12 are cut down accordingly (see "Right-sized stack" below). |
| **Two people: you and me.** | One engineer plus an assistant cannot run 14 services. Boundaries stay (as packages and separate databases), but **we ship 5 containers, not 14**, and one language (Python) for the backend. |
| **4 locations, more later: three production units and one open shop.** | `location_id` is in the model from day one for stock, sales and attendance. Adding a fifth location is a row, not a project. The layout is in "Your site" below. |
| **Track the current budget of Machines and of raw-item inventory.** | Finance holds a budget for each of those two categories and shows budget, spent, remaining and the current value (see "Budgets" below). |
| **Trade licence and GST registration: both still to come.** | No GST on any bill yet. Razorpay stays in test mode and staff record UPI payments by hand until the licence and KYC are done. Both are switches, not rebuilds (see "Licence and GST" below). |
| **Razorpay.** | `payments` is a Razorpay adapter behind a small provider interface (see "Razorpay" below). |

### Your site (4 locations)

| Location | Kind | What happens there | Stock held | Till |
|---|---|---|---|---|
| **Manufacturing Unit** (by hand) | plant | Carving, joinery, assembly by craftspeople | Wood, consumables, work in progress | no |
| **Machinery Unit** | plant | Cutting, turning, CNC. **The machines live here** | Wood, work in progress | no |
| **Painting Unit** | plant | Finishing, polish, paint | Finishing materials, work in progress | no |
| **Open Shop** | shop | Sells the finished goods; online orders are packed and delivered from here ⚠️ | Finished goods | **yes**: the counter till, UPI |

How goods move: **buy raw material → receive into a unit → transfer between units as the job moves → transfer finished goods to the Shop → sell.** Every move between units is a stock transfer (two ledger movements, with an *in transit* state), so you can always see where each piece is and what it is worth.

- **QC is a checkpoint, not a location** (confirmed). It happens in the Manufacturing Unit, as the last step before a piece goes to the Shop. A production job cannot be transferred to the Shop until a manager or achari records **QC passed**. A failed piece goes back to the unit that needs to fix it, with a reason, and the failure is counted for reporting. Nothing about this needs a fifth location.
- **The `plant_units` table from PR 67 becomes the `locations` table** (add a `kind` of plant or shop, and a `has_till` flag). The owner creates the four. The Shop gets a GPS circle too, so sales staff attendance can be checked the same way.
- **Only the Shop has a till.** Online payments go to Razorpay, not a till. Petty cash for the plants can be added later as its own account.
- Online orders reserve stock **at the Shop**, or become a production job if made to order.

### Budgets
You want the *current* budget of **Machines** and of **raw-item inventory**. Finance keeps one monthly (or yearly) budget per category:

| Category | Spent comes from | Current value shown |
|---|---|---|
| **Machines** | Machine purchases, repairs, maintenance | Net book value of the machines (cost minus depreciation) |
| **Raw items** | Raw-material purchases received | Stock on hand × weighted average cost, per unit |

Each category shows **budget, spent so far, remaining, and current value**, with an alert at 80% and 100%. Going over budget **warns, it does not block** a purchase, so the workshop never stalls on a screen. The owner sets budgets; managers read. Tools and other categories can be added as lines later. Because every spend carries a `location_id`, the same numbers can be cut per unit later without changing the model.

### Raw material sources (timber depot and reclaimed wood)
Raw wood comes from two places, and they behave differently:

| | **Fresh timber** (depot) | **Reclaimed wood** (demolished buildings) |
|---|---|---|
| How acquired | Bought on a purchase order from a supplier | Bought or collected from a demolition site, often with no supplier invoice |
| Identity | Species, grade, size; interchangeable by quantity (cft) | Each piece or small lot is **unique**: species is sometimes uncertain, with its own dimensions, condition and history |
| Provenance | Supplier and invoice | Site or building, date, who it came from, photos |
| Prep | Normal | May need de-nailing, pest or moisture treatment before use; record it |
| Cost | Invoice price | What was paid, or a **recorded estimate** if it was free. How to value free wood is a question for your CA |

Design consequences:
- Stock has two modes: **quantity items** (fresh timber, adhesive: a balance in the ledger) and **lots** (reclaimed wood: each lot has its own id, photos, dimensions, condition and cost). Both use the same append-only ledger.
- A goods receipt names the **receiving location** and the **source** (`purchased` or `reclaimed`). Reclaimed receipts do not need a supplier or invoice, but do need a provenance note.
- **Budget:** raw-item "spent" counts only **cash actually paid**. Free reclaimed wood adds stock value but no spend, so the budget is not distorted. Stock value uses the recorded cost, which can be a stated estimate.
- Production jobs consume from specific lots, so a finished piece can say "made from reclaimed teak from the 1950s house at X", which is a good product story for the storefront later.

### Licence and GST
- **Trade licence not yet issued** → Razorpay cannot go live (it needs business KYC). We build and test in Razorpay **test mode**, and staff record UPI payments by hand until then. The switch is replacing the keys.
- **GST not yet registered** → every bill is a plain consecutive "Bill" with no tax lines ✅ (already how the app works). Finance already has a tax account and a `gst_enabled` setting, off for now. Registering later means turning it on from a chosen date, which starts the tax series; earlier bills are not rewritten. Ask your CA for the turnover threshold and whether you may take online orders before the licence arrives. I can't advise on that.

### Right-sized stack (what we actually build first)

| Decision in the full design | Right-sized replacement | Why |
|---|---|---|
| 13 services | **5 app containers**: `api` (commerce + erp-core as separate packages and databases), `finance`, `worker` (notification sender, outbox relay, scheduled jobs), `media3d`, and `ai-worker` on your own machine | Finance is the one context worth a hard wall from day one (money, audit, own database). The rest stay packages until there is a reason |
| Notification in TypeScript | **Python**, inside `worker` | One backend language for two people. Still a separate process with its own queue, so a slow email provider never blocks an order |
| Keycloak | **Keep the existing, hardened auth module**; add a customer account type (email or phone OTP, order history) with separate tokens and no staff roles | Keycloak is a 0.7 GB Java app and another thing to patch. Revisit when you need Google login, MFA for many people, or more engineers |
| Redpanda | **Postgres outbox + NATS JetStream** (about 50 MB, replayable) | Same pattern as the full design, a tenth of the memory |
| ClickHouse + dbt | **Postgres views + Metabase** (add later; Metabase wants about 1 GB) | At 300 orders a day, Postgres answers every report |
| Managed Postgres | **One Postgres 16 container, separate databases per context**, nightly `pg_dump` plus WAL archiving to R2, a monthly restore test | Managed Postgres alone would eat the budget. The restore test is what makes this safe |
| Vercel for the storefront | **Next.js container on the same VM behind Caddy, with Cloudflare (free) in front** ⚠️ | Vercel's free Hobby plan is for non-commercial use, and Pro is about ₹1,700 a month. Check current terms |
| API on Vercel serverless | **API in Docker on the VM** | Serverless cannot run NATS, workers or long jobs. The move happens in step 1 below |

**Memory plan for one 8 GB VM** (estimates): Caddy 30 MB · api 400 MB · finance 200 MB · worker 200 MB · media3d 250 MB · storefront 350 MB · Postgres 1.2 GB · Redis 100 MB · NATS 50 MB → about 3 GB, leaving room for the OS, swap and Metabase later.

**Monthly cost** (rough, check current prices): VM 8 GB in Mumbai or Bangalore ₹3,000–3,500 · R2 storage and backups ₹100–300 · domain about ₹70 · Cloudflare ₹0 · monitoring (Grafana Cloud free tier, Sentry free) ₹0 → **about ₹3,500–4,000**, inside your ₹5,000. Not included, and they scale with use: Razorpay fees per payment (about 2%, check Razorpay's current pricing), SMS and WhatsApp messages, and the GPU machine you already own.

### Razorpay
- **Needs business KYC to go live** ⚠️ (your trade licence was still pending). **Test mode works without it**, so we build and test the whole flow now and switch keys when approved.
- Checkout: the backend creates a Razorpay **Order** (amount in paise, `receipt` = our order number), the storefront opens Razorpay Checkout (UPI, cards, netbanking), and the backend **verifies the signature** (HMAC-SHA256 of `order_id|payment_id`) before trusting the result.
- Webhooks (`payment.captured`, `payment.failed`, `refund.processed`) arrive with `X-Razorpay-Signature`; we verify, then dedupe on the event id. The webhook, not the browser redirect, is the source of truth.
- **Custom orders:** a Razorpay **Payment Link** for the advance, sent by WhatsApp or SMS.
- **Refunds** go through the Refunds API and post a reversing entry in Finance.
- Until KYC passes, staff record UPI advances by hand, and the same `payment.captured` event is published, so nothing downstream changes.
- The provider interface (`create_intent`, `verify`, `refund`) keeps Razorpay replaceable.

### Build order for two people
Each step ships on its own and leaves the system working.

| Step | What | Size |
|---|---|---|
| 1 | Docker images and compose on the VM; move the API off Vercel; backups to R2 with a restore test; import-linter rules for context boundaries; outbox table | M |
| 2 | Turn `plant_units` into `locations` (the 3 units + the Shop); `location_id` on stock, sales and attendance; stock transfers between units; Shop till | M. **Built** (see ONBOARDING 4b): shop kind and till flag, stock levels, transfers, location on sales, purchases and adjustments. Left for later: till sessions and cash counts, an in-transit state, placing production output, and the rename of the table |
| 3 | Customer accounts, catalog read API, storefront on the new APIs, Razorpay checkout in test mode, reservations | L. Built so far: customer accounts by emailed code (ONBOARDING 4c). The catalog read API already exists as `/public/catalog`. Still to do: orders for a signed-in shopper, saved addresses, Razorpay checkout in test mode, stock reservations |
| 4 | `finance` container: ledger and posting rules in **shadow mode** for a month, reconciled to the paisa, then cut over | L |
| 5 | Machines and tools registers; Machines and Raw-items budgets in Finance | M |
| 6 | `media3d` + `ai-worker`: scan jobs, 3D links with manager confirmation | M |
| 7 | Metabase dashboards; split `erp-core` further only if something hurts | S–M |

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
   - **Budgets** for Machines and for raw-item inventory live in Finance (§0, §6.6).
4. **Finance** sees everything: online orders, in-store sales, purchases, wages, expenses, machine depreciation. It produces invoices, a ledger, budgets versus actuals, and receivables and payables.
5. **Four locations** (Manufacturing Unit, Machinery Unit, Painting Unit, Open Shop; more later) each run the desktop app. Stock, sales and attendance are per location; only the Shop has a till (§0).
6. **Separate services**: Notification, Order, Finance, Analytics and the others below, each in Docker, tied together.
7. **Local AI model** produces 3D models from photos or scans; the output is mapped to products.
8. Plant units with GPS-verified attendance ✅ (built in PR 67; the real layout and the QC change are in §0).

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

> §0 overrides this section where they differ: with a ₹5,000 budget and two people, we ship 5 containers, not 6 deployables plus a platform stack.

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
| **identity** | Staff and customer accounts: credentials, sessions, OTP login. Phase 1: the existing auth module; Keycloak is the full-scale option (§0) | Python/FastAPI (Keycloak later) | Postgres | `api` |
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
| Notification | **Python, in the `worker` process** (full-scale option: a TypeScript service sharing templates with the storefront) | One backend language for a two-person team | Splits out later if load or ownership demands it |
| Storefront | **Next.js on Vercel** ✅ (D4) | Static pages, SEO, `<model-viewer>` for 3D | Vercel cannot run workers or the broker, so those live in Docker |
| Identity | **Phase 1: existing auth module + customer accounts. Full scale: Keycloak**, two realms | Keycloak gives OIDC, MFA, social and OTP login for free, but costs 0.7 GB and a Java component to patch. Not worth it for two people yet | Hand-rolled auth needs care; keep the audit tests ✅ |
| Event backbone | **Phase 1: Postgres outbox + NATS JetStream.** Full scale: Redpanda (Kafka API) | Durable, replayable log; Finance and Analytics can rebuild from history. NATS needs about 50 MB, Redpanda about 1 GB | Smaller ecosystem than Kafka; the event envelope is the same, so a later switch is cheap |
| Relational | **PostgreSQL 16, one database per service** | Strong consistency inside a context; the team knows it | Several instances to back up (use managed) |
| Cache / rate limits | **Redis 7** | Sessions, catalog cache, rate limits, notification dedupe | |
| Search | **Meilisearch** | Typo-tolerant product search with almost no tuning | Replaceable behind the catalog API |
| Analytics | **Phase 1: Postgres views + Metabase.** Full scale: ClickHouse + dbt + Metabase | Columnar scans for reports; SQL, not services | Do not start with ClickHouse; earn it |
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
- Valuation: weighted average cost, stored per movement so Finance can compute COGS. Reclaimed wood is a **lot** with its own cost, source and provenance (§0), and is consumed lot by lot.

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
- **The AI machine is your own computer, and it will often be switched off.** Jobs therefore wait in a queue with the status *waiting for worker*; the worker sends a heartbeat when it is up, and the UI shows whether it is online. Nothing else depends on it, and a job that was claimed but never finished is re-queued after a timeout. If you later move to a GPU server, only the worker's location changes.
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
**Email first** (decided). Launch with email only through a transactional provider (Amazon SES, Brevo or Resend, whichever is cheapest for your volume; most have a free tier, so check current limits). SMS and WhatsApp come later as extra channels behind the same interface, so adding them is a new adapter and a template, not a redesign. Customer sign-in starts as **email verification** (a one-time link or code), with phone OTP added when SMS is switched on.

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
Stock balances, sales and attendance. The till exists only at the Shop. Catalog, customers, suppliers and budgets are global; budgets can be cut by location in reports because every spend is tagged with one.

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
| Today | 1 small VPS, Postgres on it | ₹700–900 |
| **Phase 1 (the plan, §0)** | 1 VM (8 GB), Postgres container with backups to R2, Caddy, Cloudflare free | **₹3,500–4,000** |
| Growth | 2 VMs (app and data) or a managed Postgres, Metabase, bigger VM | ₹8,000–15,000 |
| Full scale | Keycloak, Redpanda, ClickHouse, managed Postgres, observability stack | ₹15,000–25,000 |
| Kubernetes / HA | multi-node, managed everything | ₹40,000+ |

The first row to move up is the database: when backups or restore time worry you, spend on managed Postgres before anything else.

---

## 13. Migration plan (strangler fig)

The build order for the two of us is in §0. The rules for each step:

- Carve code out of the running monolith into a package first (enforced by `import-linter`), then into its own container only if there is a reason.
- Every extraction is **dual-run**: the new path runs beside the old, results are compared, traffic switches by route, a rollback switch stays for one release, then the old code is deleted.
- **Finance always goes through shadow mode** for a full month and must match the existing reports to the paisa before cutover.
- Database changes stay additive (expand, then contract) ✅, with a restore-tested backup before any destructive step.
- Keep the current `/sync` endpoints until every desktop is on the command outbox (§8.3), and advertise `sync_protocol_version` ✅ (ADR-0002).

---

## 14. Risks and open questions

**Risks**
1. **Over-building for the team.** Mitigation: phase gates, 6 deployables, stop when the next split has no clear reason.
2. **Lost or duplicated events corrupt Finance.** Mitigation: outbox, idempotent consumers, daily reconciliation.
3. **Cost outruns revenue.** Mitigation: managed services only where they save real ops time; decide the budget (below).
4. **Data migration.** Moving live money data is the riskiest step; shadow mode is mandatory.
5. **Keycloak and Redpanda are new skills.** Plan time for learning, or pick the lighter options.

**Answered (2026-11-01):** budget about ₹5,000 a month · team of two · 4 locations (3 production units + 1 shop) · Razorpay · Machines and raw-item budgets · trade licence and GST both still to come · QC is a checkpoint in the Manufacturing Unit before the Shop · raw material is fresh timber from a depot or reclaimed wood from demolished buildings · AI runs on your own machine · email notifications first, SMS and WhatsApp later.

**Still open** (none blocks the first build step; defaults in brackets)
1. **Budget period and approval** (monthly; going over needs the owner's approval).
2. **AI model inputs and GPU** (decide when you pick the model; the design already copes with the machine being off).
3. **Which unit receives raw material** (chosen on each goods receipt, no default).
4. **How to value free reclaimed wood** (ask your CA; until then a recorded estimate).
5. **Online orders and delivery:** packed from the Shop, local delivery only? Any returns and refund policy? (Shop; local only; no returns policy yet.)
6. **Data protection:** legal review for customer accounts under the DPDP Act? (Ask before launching accounts.)
7. **Hosting:** preferred provider, and must data stay in India? (A Mumbai or Bangalore VM.)

## 15. What I'd revisit as it grows
- Split `erp-core` and `commerce` further once separate teams or release cadences exist.
- Move to Kubernetes when you need autoscaling or zero-downtime rollouts on multiple hosts.
- Add a data-contract layer (schema registry) when more than two teams publish events.
- Introduce gRPC for any internal call over ~50 requests per second.
- Add a service mesh (mTLS, retries) only when the number of services makes per-service libraries a burden.
- Revisit multi-tenancy (ADR-0001) if this platform is ever sold to other workshops: it is easier to design in `tenant_id` at the context boundary now than to retrofit later.
