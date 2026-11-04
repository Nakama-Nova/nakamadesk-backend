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
| Attendance | create, check-in, check-out, list all: owner, manager, sales; `POST /mark-present` (GPS): owner, manager; `GET /my`: any; `PATCH`: owner, manager |
| Check yourself in and out (`/attendance/me/check-in`, `/me/check-out`, `GET /me/today`) | any signed-in user |
| Team (`GET /team/tree`, `/team/me`, `/team/attendance`) | any signed-in user (attendance: only the people under you; the owner sees all); set who reports to whom: owner, via `PATCH /auth/users/{id}` |
| Plant units | list: any signed-in user; create, update: owner only |
| Stock by location (`/stock/levels`, `/stock/unassigned`, `GET /stock/transfers`) | owner, manager, sales |
| Stock transfers (`POST /stock/transfers`) | owner, manager |
| Machines (`/machines/*`) | owner, manager |
| Budgets: view (`GET /budgets/*`) | owner, manager; set or delete (`PUT`, `DELETE`): owner only |
| Finance ledger: view (`GET /finance/*`) | owner, manager; post, reverse, close, change accounts: owner only |
| Tools: add, change, issue, return, history | owner, manager; list and view: also achari; `GET /tools/mine`: any signed-in staff |
| Customer account (`/account/*`: profile, addresses, checkout, own orders) | website shoppers only (their own token); staff tokens are refused |
| Wages | `GET /pending`: own wages, or all for owner and manager; `POST /pay`: owner, manager |
| Orders | create, list, get: owner, manager, sales; status change: owner, manager |
| Production jobs | create, start, complete: owner, manager; list and get: owner, manager, achari |
| Purchases | create and confirm: owner, manager; list and get: owner, manager, sales |
| Sync (`/sync/push`, `/sync/pull`) | any authenticated user |

## 4. Offline sync design

- **Push** `POST /sync/push`: a batch (max 500) of `{id, entity, action, payload, updated_at}`. `id` is the client's operation UUID and is stored as `SyncLog.client_id` (unique), which makes pushes **idempotent**: replaying a batch is safe.
- Each operation runs in its own **SAVEPOINT** (`begin_nested`), so one bad operation fails alone and the rest commit. Response is `{success: [...], failed: [...]}`. Unexpected errors are logged server-side and returned as a generic message.
- Entities with handlers (`SYNC_HANDLER_REGISTRY`): `sale`, `item`, `attendance`, `raw_material`.
- **Attendance over sync** never lets the client decide the wage, pay status, plant unit or location status. A GPS-marked row is owner/manager only; see section 4a.
- **Conflicts**: last-write-wins by `updated_at` (`LWWResolver`). Stock on `item` and `raw_material` uses `StockDeltaResolver`: the payload value is an additive **delta**, not an absolute value.
- **Pull** `GET /sync/pull?last_sync=...&limit=500`: returns changed rows ordered by `(updated_at, id)` plus a `deleted` list of tombstones (`{entity, record_id, deleted_at}`) from the `sync_tombstones` table. Deleting a synced model through the repository writes a tombstone automatically.
- **Desktop side**: `services/sync_service.py` runs on a 60 s timer from `MainWindow`, pushes the SQLite outbox in batches of 200, and pulls from epoch for a full sync.

## 4a. Plant units and GPS attendance

The workshop has plant units (Manufacturing/Production, Painting/Processing, QC), each a circle in `plant_units`: `name`, `code`, centre `latitude`/`longitude`, `radius_m`, `is_active`. The owner creates and edits them (`POST`/`PUT /plant-units/`); any signed-in user can list them. Units are switched off with `is_active=false`, never deleted, because attendance rows point at them.

A manager marks someone present with `POST /attendance/mark-present` `{user_id, status, latitude, longitude, accuracy_m, client_id?}`, sending the manager's own phone position. The server does the rest:

- **GPS is untrusted input.** Latitude must be -90..90, longitude -180..180, all finite. `accuracy_m` is required and must be at most `GPS_MAX_ACCURACY_M` (default 100 m); a vaguer fix is rejected with 422 so the manager waits for a better one. The server cannot tell a spoofed position from a real one, so treat the position as a signal for the owner to review, not proof.
- **The server picks the unit**: the active unit whose circle contains the point. If circles overlap, the nearest centre wins (ties: lower `code`). The client cannot send a unit, a wage, a pay status or a date; those fields are ignored. The wage is the person's own `base_daily_wage`, and the date is today on the server.
- **Outside every unit**: the row is recorded, not rejected, with `plant_unit_id` empty and `location_status="off_site"`, and the usual pending wage. Rejecting would lose a real day's work whenever GPS drifts or a unit's circle is drawn too small; the owner reviews these with `GET /attendance/all?location_status=off_site`. Inside a unit the status is `on_site`; rows made without GPS have no status.
- **Who**: owner and manager only. SALES keeps the older attendance routes but cannot mark by GPS (the shop is not a plant unit). A manager cannot mark themselves; the owner can.
- The usual rules still apply: one attendance row a day per person, `client_id` makes a retry return the same row, and half-day pays half.
- **Offline**: an attendance sync operation that carries `latitude`, `longitude` and `accuracy_m` is checked by the same code. The row is stored against the person marked (`user_id`), recorded by the pusher, with the same wage rule and a pending wage entry. A bad position fails that one operation. Later sync updates cannot change the position, unit, wage or pay status. Attendance sync without a position behaves as before.

## 4a-2. Checking yourself in and out, and who reports to whom

**Self check-in.** Any signed-in person calls `POST /attendance/me/check-in` and `POST /attendance/me/check-out` with `{latitude, longitude, accuracy_m}` from their own phone. The position rules are exactly those of section 4a (range checks, the `GPS_MAX_ACCURACY_M` cap, the server picks the plant unit). The wage is the person's own `base_daily_wage`; the hours, half-day rule and pending wage entry are the ones of `/attendance/check-out`.

- **Away from every unit is recorded, not refused** (`location_status` / `check_out_location_status` = `off_site`): the work is not always at the workshop (a temple, a delivery). The person's manager is told (below).
- **One record a day.** A second check-in after a check-out, or on a day a manager already marked the person, is refused, because it would count the day's wage twice. The same rule now applies to the manager route `POST /attendance/check-in`.
- **`GET /attendance/me/today`** returns today's row or `null`, so the app knows whether to show Check in or Check out. A check-out is only possible against today's own open check-in; a day left open overnight needs a manager's `PATCH /attendance/{id}`.
- The check-out position is stored in `check_out_*` columns (`latitude`, `longitude`, `accuracy_m`, `plant_unit_id`, `location_status`). Sync cannot set them.
- The server cannot tell a spoofed GPS position from a real one; `off_site` is a signal to review, not proof.

**Who reports to whom.** `users.reports_to_id` holds one manager per person. The owner sets it with `PATCH /auth/users/{id}` `{"reports_to_id": "<uuid>"}` (`null` clears it, leaving it out keeps it) or at creation. It must be an active person, never the person themselves, never a loop (a manager's own manager chain cannot lead back to them), and an owner reports to no one. Promoting someone to owner clears their manager. Changes made with `PATCH` are in the audit log.

- `GET /team/tree`: the active staff as a tree, owner first (a person whose manager is switched off shows at the top).
- `GET /team/me`: my manager and the people who report straight to me.
- `GET /team/attendance?on=YYYY-MM-DD`: where each person under me stands that day (`not_in`, `in`, `out`, `marked`) and whether their own check-in or check-out was `off_site`. "Under me" is at any depth; the owner sees every active person; someone nobody reports to gets an empty list. `/attendance/all` is unchanged.
- **Notifications**: a manager (the owner, for everyone) gets an `attendance_off_site` notice when a person under them checks in or out away from every unit. A position a manager sent for someone else (`mark-present`) is the manager's own, so it never raises that notice about the person marked.

## 4b. Locations and stock by location

The business has four places: the Manufacturing Unit, the Machinery Unit, the Painting Unit and the Open Shop. They are rows in `plant_units` (the table keeps that name for now): `kind` is `plant` or `shop`, and `has_till` marks where cash is taken. The owner creates them through `/plant-units/`.

- **Totals did not change.** `items.current_stock` and `raw_materials.stock` are still the total across the business, and everything that reads them (reports, the public catalog, low-stock alerts) works as before.
- **`stock_levels`** says where part of that total is. Stock that has not been placed is **unassigned**: total minus the levels. Nothing was moved when this shipped, so all existing stock starts unassigned. Place it with a transfer from "unassigned" (`from_location_id` null) to a unit.
- **Transfers** (`POST /stock/transfers`) move stock between two places, or in and out of unassigned. They write two ledger rows that share a `transfer_id` (`transfer_out`, `transfer_in`) and leave the total alone. A transfer is the only operation that refuses to take more than is there; items move in whole numbers, raw materials in fractions. They happen instantly; there is no in-transit state yet.
- **Sales** are recorded at the location named in `location_id`, or at **the Shop when there is exactly one active shop**, else not placed. This also covers sales that arrive over sync. A sale may take a location's level below zero (sold before it was transferred there) so the counter never stops; a transfer puts it right.
- **Purchases and adjustments** can name where the goods arrived or where the count applies: `PATCH /purchases/{id}/confirm?location_id=`, `location_id` on `PATCH /items/{id}/stock` and on `POST /raw-materials/{id}/adjust`. Left out, the change is unassigned, as before.
- **Not done yet:** production consumption and output are not placed at a unit (they stay unassigned until production routing is built), there is no till session or cash count for the Shop, and the table is not renamed to `locations`.

## 4c. Customer accounts (website)

Shoppers sign in on vrikshastudios.com with a one-time code sent to their email. There is no password. The same flow creates the account the first time. This is separate from staff `users` on purpose.

- `POST /account/request-code` `{email}` always answers 202 with the same message, whether or not the address has an account. At most 5 codes an hour per address are sent, and the route is also limited per IP.
- `POST /account/verify` `{email, code, name?, accept_terms?}` returns a token. A new account needs `name` and `accept_terms: true` (the code stays valid if they are missing). A code works once, for 10 minutes, after at most 5 wrong tries; a new code replaces the old one. Codes are stored only as a keyed hash.
- The token carries `scope: "customer"` and the account's `token_version`. Staff routes refuse any scoped token, and `/account/*` refuses staff tokens, so neither works on the other's routes. Tokens last 7 days.
- `GET` and `PATCH /account/me` (name, phone as typed and **not verified**, marketing consent), `POST /account/logout-all` (ends every session), `DELETE /account/me` (removes the personal data, keeps an empty row, frees the email).
- **Email** goes through Amazon SES (`EMAIL_PROVIDER=ses`, `SES_REGION`: the server's AWS role signs in, no password to store; see [DEPLOY_AWS.md](DEPLOY_AWS.md), "Email") or through any SMTP server: set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` (`notifications@vrikshastudios.com`) and optionally `EMAIL_REPLY_TO` (`info@vrikshastudios.com`) (see `deploy/compose/.env.example`). With no `SMTP_HOST`, development writes the email to the log so you can read the code; production answers 503 rather than silently dropping codes.
- **Saved addresses:** `GET/POST /account/addresses`, `PUT/DELETE /account/addresses/{id}`. Up to 10; the first saved is the default, and deleting the default promotes the newest of the rest. Always read through the account, so another shopper's address is a 404.
- **Orders as a signed-in shopper:** `POST /account/checkout` takes the items plus a saved `address_id` or a one-off `address` (exactly one). Name and phone default from the address, then the account; the account's email is used. It runs the **same code as guest checkout** (`/public/checkout`): prices come from the server, stock and the pincode list are checked, the order is a draft and nothing is charged. The only difference is that the order records the account (`orders.account_id`). `POST /account/enquiry` does the same for a custom-piece request. `GET /account/orders` (newest first, paged) and `GET /account/orders/{number}` show only orders tied to the account, using the same cost-free `PublicOrder` shape.
- Guest checkout and `GET /public/orders/{number}?phone=` are unchanged. A guest order is **not** pulled into an account because the phone matches, since the phone is not verified.
- Deleting the account removes the saved addresses and personal data; orders stay as business records (the workshop still has to deliver them) with the customer details they already hold.
- **Verifying the phone by SMS** (signed-in shopper): `POST /account/phone/request-code` `{phone}` texts a 6-digit code (202), then `POST /account/phone/verify` `{phone, code}` sets `phone` (stored as `+91XXXXXXXXXX`) and `phone_verified_at`. Same rules as the email code (hashed, 10 minutes, once, 5 wrong tries, a new code replaces the old). Because every text costs money: at most 3 codes an hour per account, 5 a day per number whoever asks, the per-IP route limit, and **only `+91` mobiles** unless `SMS_ALLOWED_PREFIXES` says otherwise. A number already verified on another account answers 409. Changing `phone` through `PATCH /account/me` clears the verification (re-typing the verified number does not). Sending is `app/services/sms_service.py` (Amazon SNS, `SMS_PROVIDER=sns`, the server's AWS role, no keys); not set up, development logs the code and production answers 503. India needs the DLT header and template: `SMS_SENDER_ID`, `SMS_DLT_ENTITY_ID`, `SMS_DLT_TEMPLATE_ID`, with `SMS_OTP_TEMPLATE` word for word the approved text. AWS side: [DEPLOY_AWS.md](DEPLOY_AWS.md), "SMS".
- **Not done yet:** linking an account to the phone-keyed `customers` row and merging guest orders. The phone can now be verified, but pulling someone's earlier orders into an account by phone is a privacy decision (a verified number proves the shopper holds the SIM, not that the old orders were theirs), so it waits for an explicit rule. Signing in by phone instead of email is also not built.

## 4d. Paying by cash or UPI (no payment gateway) and stock holds

There is no online payment yet (no Razorpay account). The flow is: the shopper places an order (draft, nothing charged) -> staff **call to confirm it** and agree the advance -> the shopper pays **cash at the shop or by UPI** -> staff record it with `POST /orders/{id}/payments` (`method` `cash` or `upi`, with the UPI reference). A Razorpay version was built and tested in test mode and is parked on the branch `feature/razorpay-checkout`; it can be picked up once an account exists.

- `GET /public/payment-info` says how to pay: cash or UPI, the shop's UPI id and name, a phone number and the steps in words. Set `PAY_UPI_ID`, `PAY_UPI_NAME` and `PAY_PHONE` (see `deploy/compose/.env.example`).
- `upi_link` on every order view (`/public/orders/{n}`, `/account/orders`): once staff have **confirmed** the order and money is still due, a `upi://pay` link that opens a UPI app with the shop's id and the order number as the note, so staff can match the payment. It has no amount (the advance is agreed by phone) and is empty when no UPI id is set, before confirmation, when nothing is due, and on cancelled orders.
- **Stock holds** (`stock_reservations`): confirming an order (`PATCH /orders/{id}/status` to `confirmed`) holds its in-stock pieces until the order is billed, delivered or cancelled. `items.current_stock` does not move until billing; a hold only lowers what can be sold to someone else. Online checkout, counter sales and other orders' holds all see "on the shelf minus held". If the shelf has fewer than the order needs, the hold takes what is there and confirmation still succeeds. Made-to-order pieces are never held. Billing an order uses its own hold, then releases it.

## 4e. Machines and tools

Two registers, kept apart from stock (a machine or a chisel is not something we count in and out like wood).

**Machines** (`/machines`, owner and manager). Each has a `tag` (the sticker), a location, purchase date and cost, `useful_life_years`, `salvage_value` and an optional `maintenance_every_days`. Machines are retired (`PUT` with `status: "retired"`), never deleted.
- **Worth now** is straight-line depreciation by whole months down to the salvage value: `(cost - salvage) / life_years / 12 x whole months since purchase`. The response carries `accumulated_depreciation` and `book_value`. With no useful life set a machine is not written off (worth = cost); with no cost both are empty.
- **Service due**: the last `scheduled` maintenance (or the purchase date) plus the interval. `GET /machines/due?within_days=14` lists what is overdue or due soon. A `repair` does not reset it.
- **Maintenance** (`POST/GET /machines/{id}/maintenance`): `scheduled` or `repair`, the date it was done (not in the future), the cost and who did it. `maintenance_spent` on the machine is the running total. Records are never edited.
- **Downtime** (`POST /machines/{id}/downtime`, `.../downtime/end`): marks the machine `down` until ended; `GET .../downtime` is the log. A machine that is down cannot be retired until its downtime is ended; a retired one takes no maintenance or downtime.

**Tools** (`/tools`). Durable tools handed to a person and handed back; consumables such as adhesive stay in stock. A tool is `available`, `issued`, `broken`, `lost` or `retired`.
- `POST /tools/{id}/issue` hands it to an active person (409 if it is already out, naming who has it, or if it is broken, lost or retired). `POST /tools/{id}/return` takes it back as `ok`, `damaged` (becomes `broken`, condition `damaged`) or `lost`. Every issue is kept: `GET /tools/{id}/history`.
- A broken or lost tool becomes available again by `PUT` with `status: "available"` (repaired or found). A tool that is out cannot be changed until it is returned.
- `GET /tools/mine`: the tools you hold now (any signed-in staff).

Cost feeds the Machines budget (4f): the purchase cost on the purchase date and maintenance cost on the day it was done.

## 4f. Budgets (Machines and raw materials)

The owner sets how much to spend on **Machines** and on **raw materials**, for a calendar **month** or an Indian **financial year** (April to March). Only the amount is stored (`budgets`); everything else is worked out from the real records each time, so a budget cannot drift from the books.

- `PUT /budgets` (owner): `{category, period, starts_on, amount, notes}`. `starts_on` can be any day in the period; it moves to the 1st of the month, or to 1 April. Setting the same period again changes the amount. `DELETE /budgets/{id}` removes a budget, never the spending.
- `GET /budgets/current` (owner, manager): this month and this financial year for both categories. Each shows `amount` (empty if none is set), `spent`, `remaining` (negative when over), `percent_used`, `state` and `current_value`.
- `GET /budgets/warnings`: only the budgets at 80% or more, or over. `GET /budgets` lists every budget that was set, newest period first.
- **Spent, machines:** the cost of machines bought in the period (by purchase date) plus the cost of maintenance done in it (by the date it was done). Retired machines still count for the past.
- **Spent, raw materials:** the line totals of **confirmed** purchases dated in the period. Pending and cancelled purchases do not count. Wood that was collected free (a stock adjustment, no purchase) adds stock but spends nothing.
- **Current value:** machines still in use after depreciation; raw materials at stock on hand x current price (not a weighted average cost yet).
- **State:** `no_budget`, `ok`, `warning` (80% or more used), `over` (all used). A zero budget is `over` as soon as anything is spent. Percentages round half up.
- **Going over only warns.** Nothing blocks a purchase or a repair. "The owner approves going over" currently means the owner raises the budget. A request-and-approve step, per-location budgets and alerts in the notification feed are not built.

## 4g. Finance ledger (core)

A double-entry book in its own package, `app/finance/`, with its own tables (`finance_*`). The plan, the chart of accounts and the rules for posting your sales, purchases and wages are in [FINANCE_LEDGER_DESIGN.md](FINANCE_LEDGER_DESIGN.md); **only the core is built so far**. Nothing is posted automatically yet: the sales, purchase, expense and wage records do not feed the ledger until the next steps (C and D).

- **The database enforces the rules**, not only the app (`app/finance/ddl.py`, installed by the migration and by test setup): an entry has at least two lines and its debits equal its credits (checked at commit); entries and lines can **never be updated, deleted or truncated**; nothing can be posted on or before the closing date; a business record is posted once per version; a reversal points at the entry it reverses, once.
- **Accounts** (`/finance/accounts`): the starting chart is seeded by the migration (19 accounts, still awaiting the CA's approval). The owner can add accounts, rename them and switch them off; never delete. Every change is audit-logged.
- **Manual entries** (`POST /finance/journal`, owner): a date (not in the future), a reason of at least 3 characters, and balanced lines of whole paise. Each line is a debit or a credit to an account code, optionally with a location. Use it for opening balances, money the owner puts in or takes out, and corrections.
- **Reversal** (`POST /finance/entries/{id}/reverse`, owner, with a reason): posts the mirror image, dated today unless a date is given, and never inside a closed month. The original stays as it was; an entry is reversed once and a reversal is not itself reversed.
- **Reports** (owner, manager): `GET /finance/trial-balance?as_of=` (debits equal credits, `balanced` says so), `GET /finance/ledger/{code}?from=&to=` (an opening balance and a running balance), `GET /finance/entries` and `/entries/{id}`. A balance is shown in the account's normal direction (positive when it grows its usual way).
- **Closing** (`PUT /finance/closing`, owner, with a reason): closes the books through a date, or opens them again. Every change is audit-logged with who and why.
- **Posting from your records (sales and customer money):** `POST /finance/sync` (owner), or `python -m app.cli finance-sync` for a scheduled run, writes the ledger from sales and order payments. It reads the records and changes none of them, so a ledger bug cannot stop a sale. Each record is posted **once per version**: unchanged records are skipped, a changed record has its old entry reversed and the new version posted, and a sale that no longer counts (cancelled) is reversed. A reversal carries the same `source_type` and `source_id` as the entry it undoes.
  - Counter sale paid: Dr cash (cash) or bank/UPI (upi, card, bank, cheque...), Cr Sales, tax to GST payable (zero today). Unpaid: Dr customers owe us. An unclear or missing payment method goes to the clearing account and is flagged.
  - Order advance (money received before the bill): Dr cash or bank, Cr customer advances. Order billed: Dr customer advances (what was received before the bill, up to the bill) and customers owe us (the rest), Cr Sales. Payment after billing: Dr cash or bank, Cr customers owe us.
  - Entries are dated the day the record says (the sale's date; the payment's `paid_on`), which is also how the profit report counts them.
  - **Things it cannot know** are listed by `GET /finance/exceptions` (a dry run): `partial_sale_amount_unknown` (a part-paid counter sale does not say how much was paid), `payment_method_unclear`, `order_missing`, and `cannot_post` (the month is closed). A change to a record in a closed month is reversed today, and the new version is reported, not posted.
- **Reconciliation** (`GET /finance/reconciliation?from=&to=`, owner and manager): for each month, the profit report's sales, purchases, wages and expenses against the ledger's, and the order payments recorded against what the ledger took in from them, plus how many records are still to post or reverse. `agrees` is true only when every difference is zero and nothing is waiting. This is the check for the shadow month.
- **Costs (same sync):**
  - Purchase confirmed: Dr raw material bought, Cr we owe suppliers, on the purchase date. A cancelled purchase is reversed.
  - Purchase marked paid: Dr we owe suppliers, Cr unmatched payments (the app records no supplier payment date or method), flagged `supplier_payment_unrecorded`. A part-paid purchase stays owed and is flagged `partial_purchase_amount_unknown`.
  - Expense: Dr the category's account (repairs and maintenance, other costs, or unsorted), Cr cash or bank/UPI by the method. A voided expense is reversed.
  - Pay-out to staff: Dr wages (gross), Cr cash or bank/UPI (net paid) and staff advances (taken back). Old-style `/wages/pay` payments: Dr wages, Cr cash. A pay-out that pays more than was earned is reported (`payout_does_not_add_up`), not posted.
  - Staff advance given: Dr staff advances, Cr cash. Advance paid back on its own: the reverse. Money taken back inside a pay-out is already in that pay-out.
  - Machine bought, machine repair or service: against unmatched payments, flagged `machine_payment_unrecorded`; they are not part of the profit report, so the reconciliation leaves them out.
- **Reports and the accountant role:**
  - `GET /finance/profit-and-loss?from=&to=` (owner, manager, accountant): income and expenses by account over a period, and the net profit. It matches the existing profit report to the paisa once everything is posted (tax is not income).
  - `GET /finance/balance-sheet?as_of=` (owner, accountant): what the business has, owes and is worth on a day. The profit made so far is its own line (the books are never closed into equity), so assets always equal liabilities plus equity.
  - The new **`accountant`** role is for your CA: it can read every finance route and report and change nothing (no sync, journal, accounts or closing). Create the user with role `accountant`. Until the shadow month is signed off these are previews; the existing reports stay official.
- Not built yet: depreciation, GST, the small data fixes (sale collections, supplier payments, cost on sale lines).

## 4h. Editing and deleting bills and orders

Nothing that touched money is erased. "Delete" means: undo what it did, and keep the record with the reason.

- **Delete a bill** (`DELETE /sales/{id}?reason=...`, owner): every catalogue piece goes back on the shelf, to the location it left, with a `finished_in` stock movement pointing at the bill. The bill stays in the history as `cancelled`; the sales reports (sales, top products, profit and loss, GST, the Excel export) and the profit report stop counting it; the finance ledger reverses it on the next sync. Cash paid across the counter is treated as given back. A bill made from an order frees the order to be billed again (and holds its stock again); the customer's payments stay on the order. Once only, and the reason goes to the audit log.
- **Edit a bill** (`PATCH /sales/{id}`, owner or manager): the customer, and the payment status and method of a counter bill. What was sold and the prices are fixed: cancel the bill and make a new one. A bill made from an order follows the payments on its order.
- **Orders**: `PATCH /orders/{id}/payments/{pid}` corrects a payment (owner); `POST /orders/{id}/payments/{pid}/void` takes a wrong one off with a reason (owner). It stays on record, and the order's paid total, its bill and the ledger follow. `DELETE /orders/{id}` removes an order that never went anywhere; one with a bill, payments, production jobs or letters returns 409 (cancel it instead).
- **Change the pieces on an order** (`PUT /orders/{id}/items`, owner or manager): send the whole list; a piece with its `id` is changed in place, one without is added, one left out is removed. Send `estimated_amount` if the price moved (it stays as it is otherwise, and cannot go below what is already paid). The stock held for a confirmed order is set again for the new pieces. Refused with 409 for a billed order (cancel the bill first, since it was made from these pieces), a delivered or cancelled order, and taking off a piece that has a live production job.
- Run `python -m app.cli finance-sync` (or `POST /finance/sync`) to bring the ledger up to date after any of these.
## 4i. Editing letters, and correcting ledger entries by hand

- **Edit a letter** (`PUT /letters/{id}`, whoever may issue that kind): send the same details as when it was issued. The number, the issue date and the letterhead it was printed on stay; the content is rebuilt (an order confirmation picks up the order as it is now). The old wording goes to the audit log. A void letter cannot be edited.
- **Delete a letter** (`DELETE /letters/{id}`, owner): only a void one, so every number handed out has a story. What it said is kept in the audit log.
- **Correct a ledger entry by hand** (`PUT /finance/entries/{id}`, owner): for entries the owner typed in (`manual`). Send the corrected date, memo and lines with a reason. The old entry is reversed (on its own date, or today if that month is closed) and the corrected one posted, in one step; the ledger is never rewritten, and the new entry says what it corrects. An entry made from a sale, expense, payment or other record returns 409: correct that record and sync (typing over it would be undone by the next sync), or post an adjusting journal entry.

## 5. Running it locally

To run it the way production does (Docker, Caddy, Postgres, backups), see [DEPLOY.md](DEPLOY.md). The first-login command there is the same `python -m app.cli create-owner`, run with `docker compose exec api python -m app.cli create-owner`.

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

Env vars: `ENV` (default `dev`), `DATABASE_URL`, `DEV_DATABASE_URL`, `TEST_DATABASE_URL`, `SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES` (default 60). Website API: `MEDIA_DIR` (uploaded photos/models, default `media`), `DELIVERY_PINCODES` (comma separated; empty = no restriction), `CORS_ORIGINS` (storefront origins, comma separated; empty = none), `PUBLIC_RATE_LIMIT` (calls per IP per minute, default 20). Private object store (scans, STL/OBJ/G-code, part models): `S3_BUCKET`, `S3_ENDPOINT_URL` (empty for AWS S3; the account URL for Cloudflare R2), `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`. With `S3_BUCKET` empty the upload endpoints answer 503. Keep the bucket **private** and give the key access to that one bucket only. Suggested lifecycle rule: delete `scans/` source files 60 days after the job is done (keeps storage cost flat). Outside `dev`/`test`, startup **refuses to run** with a missing or default `SECRET_KEY`. `conftest.py` forces `ENV=test`; fixtures `auth_client`, `manager_client`, `worker_client` give pre-authenticated clients. **`pytest` wipes the database it points at** (`DROP SCHEMA public`), and `DATABASE_URL` from `.env` wins over `TEST_DATABASE_URL`: run it with `DATABASE_URL` set to a separate test database, never your dev one. Alembic has a single head (`d0e1f2a3b4c5`); keep it linear.

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
- Take things out in two deploys: first ship code that stops using the table or column, then drop it in a later migration. Old code still running during a deploy would otherwise query a column that is gone. Brands followed this: PR 67 removed the code and API, and migration `e3f4a5b6c7d8` then dropped the `brands` table and `items.brand_id`.
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

The desktop installers (and the phone app's `.apk`) are GitHub Releases of `nakama-desktop` and `vriksha-mobile`. The app asks this server (`GET /app/updates/latest`, then `/app/updates/download/{version}`), which reads the releases and only answers signed-in staff.

**No setup is needed while those repos are public**: the server asks GitHub without a token (60 questions an hour per server address; answers are cached for 15 minutes, so that is plenty). If a repo is made private again, or you want a higher limit, add a token (it is a secret: enter it yourself, never in git or chat):
1. GitHub, Settings, Developer settings, **Fine-grained personal access tokens**, Generate new token. Resource owner: Nakama-Nova. Repository access: only the repo(s) concerned. Permissions: **Contents: Read-only** (Metadata is added automatically). GitHub caps the expiry at one year.
2. Put it in the server settings as `GITHUB_RELEASES_TOKEN`, then redeploy. `GITHUB_RELEASES_REPO` defaults to `Nakama-Nova/nakama-desktop`.
3. When a token expires the endpoint answers 502 "The update token was refused": make a new one and replace the setting. Without a token a refusal reads "GitHub is not answering update checks right now" (usually the hourly limit): wait a few minutes.

A repo with no published release simply means no update is offered.
