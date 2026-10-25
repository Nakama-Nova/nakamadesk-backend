# Vriksha Studios — Unified System Design

**Status:** Draft v1 · **Date:** 2026-10-25 · **Scope:** backend + desktop POS + Flutter app + public website/shop (wood furniture and wood carving art). 3D models per product come later. The scan → CNC pipeline is parked (see `VrikshaStudio/`).

Legend: ✅ verified in the repo · ⚠️ assumption, please confirm.

---

## 0. Owner answers (2026-10-25) — these override anything below that conflicts

| Fact | Consequence |
|---|---|
| Budget **≈ ₹1,000/month**. AWS not started. | RDS and managed Postgres are out. **One 2 GB VPS runs the API and Postgres together** (~₹500–800/month, ⚠️ check current price at sign-up, Mumbai or Bangalore region). Nightly `pg_dump` to R2. Vercel and R2 stay on free tiers. A `.in` domain is ~₹800/year. Estimated total **₹700–900/month**. A 1 GB t3.micro is too small to share with Postgres. |
| **One shop, one machine.** | The desktop is a single terminal. Multi-terminal sync conflicts are rare, so `/sync` stays as it is. The UUID outbox fix still goes in (it is cheap), but it is no longer urgent. Internet drops are the real risk. |
| **Local delivery only** at launch. | No courier integration and no shipping rates. Checkout takes a pincode and checks it against an allow-list in config. Delivery is by your own team. |
| **Not GST-registered yet, trade licence pending.** | You **must not charge or show GST** until registered. Keep `gst_percent = 0` on items. Issue plain bills, numbered consecutively from day one so the series carries over. Website says "final price", not "inclusive of GST". Registration is required once turnover crosses the threshold for your state, so ask your CA for the exact figure. |
| **No payment gateway in v1.** | Gateways ask for business proof (trade licence), which you don't have yet. The advance is paid by UPI to the shop's QR or number, and staff record it on the desktop with `Order.advance_paid` (already in the model). **`payments.py`, the webhook and the `payments` table move to a later phase.** |
| Kotlin app can go. Flutter replaces it. | Confirmed. |
| Figma is on the free plan. | Claude can still draw in files you own. Keep everything in the one file with several pages so you stay inside the free limits. |

**Revised v1 checkout:** the website takes an order or an enquiry → staff get it on desktop or mobile → they call or WhatsApp to confirm and agree the advance → the customer pays by UPI → staff mark the advance received → the order moves to `confirmed`.

---

## 1. Decisions in one screen

| # | Decision | Why |
|---|---|---|
| D1 | **Keep one FastAPI monolith.** Add modules to it. No microservices, queues or API gateway. | A t3.micro has 1 GB of RAM, there are two counters, and the sync engine only just stabilised. The earlier 6-service spec would cost more to run than the shop earns. |
| D2 | **The website talks to the same backend through a new unauthenticated `/public/*` router** with its own response schemas. | One source of truth for stock and prices. The schemas stop `purchase_price` and `production_cost` leaking. |
| D3 | **Flutter replaces the native Kotlin app.** ✅ The Kotlin app only has login and a dashboard, so little is lost. It is **online-first**, with no offline outbox in v1. | You asked for Flutter. Mobile has no sync today (ADR-0001). The desktop is the offline terminal. |
| D4 | **Storefront is Next.js on Vercel.** Catalog pages are statically generated and refreshed (ISR) from `/public/catalog`. | Cheap, fast, and good for Google search, which carvings need. |
| D5 | **Customers check out as guests, identified by phone.** There are no customer accounts in v1. | `customers` is already phone-keyed (`GET /customers/search`). Indian buyers expect phone and UPI, not passwords. |
| D6 | **Images and 3D files live in object storage (Cloudflare R2 or S3), not on the EC2 disk.** One `product_assets` table holds image, glb, stl and nc files. | The t3.micro disk is small, R2 has free egress, and this table is where the later 3D and CNC files go. |
| D7 | **Show 3D with Google's `<model-viewer>` web component, not a Three.js app.** | One HTML tag gives rotate, zoom, AR on phones, and `.glb` support. |
| D8 | **Figma designs the screens, Canva makes the marketing and product imagery, and one colour and font token file feeds all clients.** | Details in §9. |

---

## 2. What exists today ✅

```
nakamadesk-backend (FastAPI 0.129, SQLAlchemy 2, Alembic, Postgres, JWT HS256)
  routers: auth, items, sales, customers, invoices, orders, production, purchases,
           raw_materials, bom, attendance, wages, reports, dashboard, sync, health
  models:  Item(version_id, GST/HSN, image_url), Order(standard|custom, JSONB custom_specs,
           advance_paid), ProductionJob, BOM, InventoryMovement, Sale, Customer, SyncLog, AuditLog ...
  sync:    POST /sync/push (idempotent via SyncLog.client_id, max 500 ops), GET /sync/pull
           (LWW conflicts, tombstones). Entities: sale, item, attendance, raw_material.
  ADRs:    0001 single-tenant per deployment, 0002 no URL versioning (protocol-version signal instead)
nakama-desktop    PyQt6 POS. Local SQLite outbox exists, but nothing feeds it (your issues 8 and 9).
furnibiz-android  Native Kotlin/Compose. Login and dashboard only.
VrikshaStudio     CLI: mesh or depth image → 3-axis G-code. Works, but isn't safe to cut with yet.
```

**Gaps this design has to close**
1. **No public/customer surface.** Every route needs a staff login, and `Item` responses include cost fields.
2. **`Item` is an inventory record, not a catalog entry.** It has one `image_url`, and no slug, description for shoppers, "made to order" flag, lead time, or 3D asset.
3. **No payment record.** `Order.advance_paid` is a number with nothing behind it.
4. **GST invoice numbering.** `NTD-YYYY-<8 random chars>` (`invoice_service.py:18`) is not consecutive. GST rules require a consecutive serial per financial year. **Fix this before the website goes live**, because the web will multiply invoice volume. ⚠️ Confirm with your CA.
5. **Desktop offline sync is unwired**, and its outbox key isn't a UUID. This is the core of the POS.
6. **Auth hygiene.** `/auth/register` should reject unauthenticated attempts to create an owner, and the dev `SECRET_KEY` fallback was only just fixed.
7. **Order lifecycle.** ⚠️ Not verified whether `PATCH /orders/{id}/status` blocks illegal jumps such as `delivered → draft`.

---

## 3. Requirements

**Functional**
- *Customer:* browse furniture and carvings, search and filter, see photos (later 3D), buy in-stock items online, request a custom piece, track an order with order number and phone.
- *Sales/Owner:* the same product list drives the POS and the website. Quote custom work, take an advance, and see web orders next to counter orders.
- *Workshop:* a confirmed order creates production jobs (already modelled) for the achari and workers.
- *Mobile:* dashboard, stock lookup and adjust, orders and status, production jobs, attendance check-in, quick sale.

**Non-functional**
- Cost: ≲ ₹1,500/month all-in at launch (⚠️ my guess, tell me your budget).
- Scale: tens of orders a day, hundreds of products, thousands of page views a day. Static pages absorb almost all of this.
- Availability: the counter must work offline (desktop), and the website can go down for minutes without losing money.
- Money: `Numeric`/`Decimal` only, never float. This is already the repo's rule.
- Compatibility: additive Alembic migrations only, so old desktop installs keep working.

---

## 4. Architecture

```
 Customers                        Staff
    │                               │
 ┌──▼───────────────┐   ┌───────────▼───────────┐   ┌──────────────────┐
 │ Next.js storefront│   │ PyQt6 desktop POS     │   │ Flutter app      │
 │ (Vercel, ISR)     │   │ SQLite outbox, offline│   │ online-first     │
 └──┬───────────────┘   └───────────┬───────────┘   └────────┬─────────┘
    │ /public/*  (no auth, rate-limited)   │ /auth, /sync/*, all staff routes
    └───────────────┬───────────────────────┴────────────────────┘
                    ▼
      Caddy (TLS) ─▶ FastAPI monolith  (EC2 t3.micro, Docker)
        ├ public.py    ← NEW: catalog, cart-less checkout, order tracking
        ├ payments.py  ← NEW: Razorpay order + signed webhook
        ├ assets.py    ← NEW: presigned upload/download URLs
        └ existing: auth items orders production sales invoices sync ...
                    │                        │
        Postgres (RDS free tier or Neon)   R2/S3: images, .glb, later .nc/.stl
                                             ▲
   Figma / Canva ──(designs, exports)────────┘   (human workflow, not runtime)
```

**Request path for a web purchase**
1. The storefront lists `GET /public/catalog` (cached by ISR, so no backend call per visitor).
2. The customer fills in name, phone, address and cart, then `POST /public/checkout`. In one DB transaction the backend upserts the `Customer` by phone, creates an `Order(source='web', status='draft')`, and creates a `Payment(status='created')`. It then calls Razorpay to create a payment order and returns its id.
3. The customer pays with UPI or card in Razorpay's widget.
4. Razorpay calls `POST /payments/webhook`. The backend verifies the HMAC signature and dedupes on `provider_ref` (idempotent, like `SyncLog.client_id`). It marks the payment `paid`, then moves the order `draft → confirmed`. For in-stock items it writes an `InventoryMovement`, and the existing `version_id` lock prevents overselling. For made-to-order items it creates a `ProductionJob`.
5. The customer tracks the order at `GET /public/orders/{order_number}?phone=...`. The phone must match, so order numbers can't be enumerated.

**Custom carving or furniture (the high-value flow)**
Enquiry form (wood, size, motif, reference photos) → `Order(order_type='custom', draft, custom_specs=JSONB)` → staff quote on desktop or app (`estimated_amount`) → WhatsApp or SMS the customer a Razorpay payment link for the **advance** → paid → `confirmed` → production. This fits the existing `Order` model, which already has `custom_specs`, `reference_image_url` and `advance_paid`.

---

## 5. Data model — additive changes only

```sql
-- items: catalog fields (all nullable or defaulted)
ALTER TABLE items ADD slug text UNIQUE;
ALTER TABLE items ADD is_published boolean NOT NULL DEFAULT false;
ALTER TABLE items ADD made_to_order boolean NOT NULL DEFAULT false;
ALTER TABLE items ADD lead_time_days int;
ALTER TABLE items ADD short_desc text;          -- shopper-facing; `description` stays internal
ALTER TABLE items ADD wood_type text;
ALTER TABLE items ADD dimensions_mm jsonb;      -- {l,w,h}

-- one table for every file attached to a product
CREATE TABLE product_assets (
  id uuid PK, item_id uuid FK items,
  kind text,            -- image | glb | stl | nc | source_scan
  storage_key text,     -- R2/S3 object key, never a full URL
  sort int DEFAULT 0, alt text, bytes int, sha256 text, created_at timestamptz
);

-- web/app provenance and idempotency
ALTER TABLE orders ADD source text NOT NULL DEFAULT 'pos';   -- pos | web | app
ALTER TABLE orders ADD delivery_address jsonb;

CREATE TABLE payments (
  id uuid PK, order_id uuid FK orders,
  provider text, provider_ref text UNIQUE,   -- idempotency key
  amount numeric(12,2), status text,         -- created | paid | failed | refunded
  raw jsonb, created_at timestamptz
);

-- GST-compliant invoice numbering (fixes gap 4)
CREATE TABLE invoice_counters (fy text PRIMARY KEY, last_no int NOT NULL);
-- number = f"NTD/{fy}/{n:05d}" via UPDATE ... RETURNING under row lock
```

Notes:
- `Item.image_url` stays for old clients. New code reads `product_assets`.
- The catalog fields live on `Item`, not a new `Product` table. There's one SKU and one stock count, and a second table would just have to be kept in sync (ADR-0001's "avoid unnecessary enterprise complexity").
- Because `Item` fields sync to desktop, **do not** add the new catalog columns to the sync payloads yet. Staff edit catalog data online. Sync stays about stock and sales.
- **CNC link, later:** a `product_assets` row with `kind='nc'`, plus `meta jsonb` for width, tool and feed. That is all the CNC pipeline needs from the ERP, so nothing is built for it now.

---

## 6. API surface (new)

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /public/catalog?category=&q=&page=` | none | Published items: slug, name, price incl. GST, images, made-to-order, lead time. **No cost fields.** |
| `GET /public/catalog/{slug}` | none | One product, with assets (glb when present). |
| `POST /public/checkout` | none, rate-limited | Cart + customer + address → order + Razorpay order id. |
| `POST /public/enquiry` | none, rate-limited | Custom order request → draft custom order. |
| `GET /public/orders/{number}?phone=` | phone match | Order status. |
| `POST /payments/webhook` | HMAC signature | Razorpay events. |
| `POST /assets/presign` | staff | Presigned upload URL for R2/S3. The server never proxies big files. |
| `POST /orders/{id}/payment-link` | staff | Advance payment link for custom work. |

Everything else is existing staff routes. Per ADR-0002 there is no `/v1` prefix. The storefront is deployed together with the backend, so it can't drift the way an installed desktop can.

---

## 7. Clients

**Desktop (finish first).** Give each outbox row a UUID (`client_id`). Call `queue_operation` from the sales and inventory writes. Push and pull when `is_online()` flips to true. Decide on token persistence (issue 10). Then add a read-only "web orders" list, which is just `GET /orders?source=web`.

**Flutter app.**
- Packages: `dio`, `flutter_riverpod`, `go_router`, `flutter_secure_storage`. Nothing else until a screen needs it.
- Screens, in order: login → dashboard → items (search, stock +/−) → orders (list, status change) → production jobs → attendance check-in → quick sale.
- Same endpoints and the same RBAC as the desktop. The app does no business logic of its own.
- Offline: cache the last GETs only. If the achari needs true offline later, reuse `/sync/push` with a UUID outbox in `drift`. The server side already exists.

**Storefront.** Next.js App Router, Tailwind driven by the shared tokens. Pages: home, category, product (with `<model-viewer>` when a glb exists), cart, checkout, custom-order form, track-order, plus About, Contact and a WhatsApp button. Use JSON-LD `Product` markup for Google. Product pages are statically generated with a revalidate of about 10 minutes. A "stock changed" webhook can trigger revalidation later.

---

## 8. Infra, cost, security

**Hosting** ⚠️ (assumptions, adjust to your AWS account)
- EC2 t3.micro running Docker compose: `caddy` and `api`. Add 1 GB of swap.
- Postgres: RDS db.t3.micro if free tier applies, otherwise Neon or Supabase free. **Don't** run Postgres on the 1 GB box beside the API.
- Nightly `pg_dump` to R2, with a restore test once a month.
- Vercel hobby for the storefront. R2 free tier for files.

**Security**
- Separate public and staff trust zones. `/public/*` is read-only apart from checkout and enquiry, gets per-IP rate limits (e.g. `slowapi`), and uses a CORS allow-list of the storefront origin.
- Razorpay: verify the webhook HMAC and confirm the amount server-side. Never trust the amount from the browser.
- Lock `/auth/register` (staff only) and keep the fail-fast `SECRET_KEY`.
- Uploads: presigned URLs with a size and content-type cap. Customer reference images go to a quarantine prefix.
- Skip RS256 and an API gateway. They only help with several verifying services.

**Observability:** structured request logs (`TimingMiddleware` exists), `/health`, one uptime check, and Sentry free tier. Add nothing else.

---

## 9. Canva and Figma

State today ✅: both are connected. **Figma:** the account "Vriksha Studio" is on a starter team with a **View seat**, so Claude can read files but probably can't create or edit them. A Full seat is needed for that. **Canva:** no Brand Kit and no designs exist yet.

| Tool | Used for | Output |
|---|---|---|
| **Canva** | Logo, brand kit (colours, fonts), product photo clean-up (background removal), catalogue, banners, Instagram and WhatsApp creatives. | PNG/JPG into R2, with the palette and fonts exported as tokens. |
| **Figma** | UI layouts for the storefront and the Flutter screens, so the three clients look alike. | Frames, then `get_design_context` for Claude to generate Tailwind and Flutter code from them. |
| **Repo** | `design-tokens.json` (colours, type, spacing). Tailwind and the Flutter `ThemeData` both read it. | One edit changes all clients. |

First screens (v1, placeholder photos and sample prices): https://www.figma.com/design/w1BtxCfC87A5pBDx4lrP3Z. The brand colours are Figma variables in the `Brand` collection; the same values are in `docs/brand/design-tokens.json`.

Order of work: create the Canva Brand Kit first (nothing downstream can start without palette and logo) → tokens → Figma screens → code.

---

## 10. Trade-offs and what I'd revisit

| Choice | Gives up | Revisit when |
|---|---|---|
| Monolith | Independent deploys and scaling | Slicer/AI workloads or a second team arrive (then split only the slicer, as a worker). |
| Guest checkout, no accounts | Order history, wishlist, repeat-buyer convenience | Repeat customers are a real share of sales (add phone OTP login). |
| Online-first mobile | Works badly in a dead zone | Achari or workers need it on the shop floor (reuse `/sync`). |
| ISR static catalog | A price change can show up to ~10 min late | Flash sales (add on-demand revalidation). |
| `model-viewer` | Less control than Three.js | A configurator (choose wood or finish live) is wanted. |
| Razorpay only | Lock-in, and ~2% fee | Volume makes the fee matter (UPI QR direct for in-person). |
| Single-tenant (ADR-0001) | Per-business infra | A second business wants the platform. |
| Shipping not integrated | Manual dispatch | Courier shipments exceed ~10/week (Shiprocket or Delhivery API). |

**Risks**
1. **Scope.** Four clients at once will stall. The order in §11 exists to prevent that.
2. **GST numbering.** A compliance risk until the counter is fixed.
3. **Shipping large furniture.** It is a logistics problem, not a software one. Local delivery around Puttur and Thorur for furniture, courier for small carvings, and the site should say so.
4. **Photo quality.** A carving shop sells on images. Plan a proper photo day before launch. This matters more than any code here.

---

## 11. Suggested build order (input for `/plan`)

1. **Backend hardening:** invoice counter, register lockdown, order-status transition guard, Alembic migration for §5.
2. **Desktop:** UUID outbox, wire sync, token decision.
3. **Brand:** Canva Brand Kit and tokens, then the first Figma frames.
4. **Public API and assets:** `/public/*`, `/assets/presign`, product publishing from desktop or app.
5. **Storefront MVP:** catalog, product, enquiry form (no payments yet). This is already sellable through WhatsApp and phone.
6. **Payments (deferred until you have a trade licence and GST registration):** Razorpay checkout, webhook, payment links. Until then, staff record UPI advances by hand.
7. **Flutter app,** screen by screen in the order from §7.
8. **3D models:** upload glb per product, then show `<model-viewer>`.
9. **Scan → CNC,** after the above.

---

## 12. Open questions

Answered in §0: budget, shipping, GST, Kotlin app, Figma plan. Still open:

1. **Domain name** for the store (or should I suggest some?).
2. **WhatsApp number** for the "Enquire on WhatsApp" button. Is it a WhatsApp Business account?
3. **Which delivery pincodes or towns** count as local?
4. **Which state** is the shop registered in? This sets the GST registration threshold.
5. Does the shop's **UPI ID or QR** go on the order confirmation page?
