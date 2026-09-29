# ADR-0001: Multi-Tenancy Strategy

**Status:** Proposed
**Date:** 2026-09-29
**Deciders:** Product owner, backend tech lead

## Context

Nakama Nova's master engineering prompt (§11, Multi-Tenancy) asks explicitly:
"Evaluate whether Nakama Nova should support multiple businesses/organizations,"
with the target hierarchy `Organization → Users → Roles → Permissions →
Business Data`, and ensuring users can't access another organization's
products, inventory, customers, suppliers, sales, purchases, reports, or
financial data.

That evaluation has never been made explicitly — the schema has simply
grown single-tenant by omission. Concretely, today:

- There is no `Organization` (or `Business`/`Tenant`) entity anywhere in
  `app/models/`.
- Several tables enforce **globally** unique constraints that only make
  sense within one business: `items.sku`, `raw_materials.name`,
  `brands.name`, `categories.name`, `users.username`, `users.email`,
  `sales.invoice_number`, `orders` number, `production_jobs` number.
  If two businesses ever shared one backend deployment, they would
  collide on SKUs, item names, invoice numbers, and usernames.
- RBAC (`UserRole`: owner/manager/sales/achari/worker) scopes a user's
  *permissions within a business*, but nothing scopes *which business*
  a user, request, or sync operation belongs to.
- The desktop client's `.env` (`API_BASE_URL`) points at exactly one
  backend URL per install — the deployment model today is already
  "one backend + one database per business," just not documented as
  a decision.
- The offline sync engine (recently stabilized — see #17-#23) has
  already shown that scoping bugs are easy to introduce and expensive
  to find (attendance sync scoped by the wrong column, cross-user
  update/delete checks). Adding a *second* scoping dimension
  (organization) on top of the existing user-scoping in
  `GenericSyncHandler`/`get_by_id_scoped` would multiply that same
  bug surface across every synced entity, at a time when that code
  just stabilized.
- The project is still in early phases of its own roadmap (§44):
  offline sync (Phase 4) isn't fully wired end to end yet (#1, #4
  open), and mobile has no sync/offline capability at all. No
  multi-business hosting demand has been identified.

The question this ADR answers: **should Nakama Nova move to shared,
row-level multi-tenancy (one deployment serving many businesses) now,
or continue with one deployment per business, and defer shared
multi-tenancy until there's a concrete reason to take it on?**

## Decision

**Stay single-tenant per deployment for now.** Each business gets its
own backend instance and database (self-hosted or one
managed-hosting slot per customer). Do not add an `Organization`
entity or `organization_id` scoping to the schema, RBAC, or sync
engine at this time.

Revisit this decision when at least one of the following becomes true:
- A hosting/business model emerges that specifically requires shared
  infrastructure across businesses (e.g. a low-cost hosted tier where
  per-business database provisioning is no longer economical).
- The number of businesses to onboard makes "one Postgres instance
  per business" operationally unmanageable (a number worth naming
  concretely when it's actually approached, not guessed at now).
- A customer requires cross-business reporting/rollups (e.g. a
  franchise or multi-branch owner wanting one login across branches)
  — note this is a narrower need than full shared multi-tenancy and
  may have a cheaper solution (see Option C below) if it comes up
  first.

## Options Considered

### Option A: Single-tenant per deployment (status quo, formalized)

One backend process + one Postgres database per business. No schema
change. Isolation is physical (separate infrastructure), not
enforced by application code.

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — this is already how it works today |
| Cost | Scales linearly with business count (DB + compute per business); fine at current scale, gets expensive in the hundreds+ |
| Scalability | Scales in business *count* by adding deployments, not by scaling one deployment |
| Team familiarity | High — no new pattern to learn |

**Pros:**
- Zero migration risk to a sync engine that just stabilized.
- Strongest possible tenant isolation — a bug can't leak data across
  businesses because there's no shared process or database to leak
  across.
- Matches the master prompt's own guidance: "avoid unnecessary
  enterprise complexity unless there is a clear business reason"
  and "do not implement scaling infrastructure before it is
  justified" (§34).

**Cons:**
- Higher marginal hosting cost per business than shared infrastructure.
- No built-in cross-business reporting for an owner with multiple
  locations.
- Onboarding a new business means provisioning new infrastructure,
  not just a new row.

### Option B: Shared multi-tenancy (Organization → Users → Business Data)

Add an `Organization` entity; add `organization_id` to every
business-data table; scope every query, every RBAC check, every sync
operation, and every report by the current user's organization.

| Dimension | Assessment |
|-----------|------------|
| Complexity | High — touches essentially every model, every repository method, the entire sync push/pull path, and every report query |
| Cost | Lower marginal cost per business once built; high upfront build + ongoing correctness cost |
| Scalability | Scales well in business count on one deployment; requires careful index/query design as data grows |
| Team familiarity | Matches the pattern described in the master prompt (§11), but the team hasn't built this scoping layer before |

**Pros:**
- Matches the "real commercial SaaS" long-term framing in the master
  prompt.
- Cheaper hosting per business at scale.
- Enables cross-business features later (shared catalogs, franchise
  reporting) if ever needed.

**Cons:**
- `organization_id` scoping is exactly the class of bug tonight's
  session spent 7 commits fixing (attendance scoped by the wrong
  column, cross-user access via `get_by_id_scoped`) — adding a
  second, orthogonal scoping dimension on top of user-scoping
  meaningfully raises the chance of a cross-tenant data leak, which
  is a much worse failure mode than the bugs just fixed.
  Every one of the 10 issues fixed tonight would need re-auditing
  under org-scoping.
  Every unique constraint (`sku`, `invoice_number`, usernames, etc.)
  needs to become a composite `(organization_id, field)` constraint —
  a real data migration, not just an additive column.
- desktop and mobile clients would need to carry and send an
  organization context through login, sync, and every API call.
- No current business requirement is driving this; building it now
  is speculative.

### Option C: Schema-per-tenant (database-level isolation, shared app)

One backend codebase, but each business gets its own Postgres schema
(or database), selected by subdomain/API key at connection time.
Isolation is enforced by Postgres, not application `WHERE` clauses.

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium-high — needs dynamic connection/schema routing and per-tenant migration tooling |
| Cost | Lower than Option A at scale (shared compute), higher than Option B (still N schemas to migrate/manage) |
| Scalability | Good for isolation; migrations must run against every tenant schema |
| Team familiarity | Low — new pattern, not currently used anywhere in the stack |

**Pros:**
- No `organization_id` scoping bugs possible — Postgres enforces the
  boundary, not application code. Meaningfully safer than Option B
  for the same underlying goal.
- Cheaper than Option A per business once the routing layer exists.

**Cons:**
- Alembic migrations need to run per-tenant-schema, not once —
  operational complexity of its own.
- Still a real engineering project to build the routing layer, for a
  need that isn't confirmed yet.
- Cross-business reporting is still hard (queries can't span schemas
  without extra plumbing).

## Trade-off Analysis

The real question isn't "which multi-tenancy pattern is best in the
abstract" — it's "has anything happened yet that justifies taking on
*any* of them." Nothing has: there's no hosting-cost pressure, no
confirmed customer asking for shared/franchise access, and the
codebase's highest-risk area (sync scoping) just went through 7
bug-fix commits for exactly the kind of scoping mistake Option B would
add more of. Building B or C now would be optimizing for a business
model that hasn't been chosen yet, at the cost of destabilizing code
that just stabilized.

Option A is not "no plan" — it's the correct default until a concrete
trigger (named in Decision, above) appears. If that trigger is
cross-business reporting specifically (an owner with multiple
locations), Option C's isolation properties make it the better
fallback over Option B, since it doesn't add row-level scoping bugs to
the sync engine.

## Consequences

- Onboarding a new business means provisioning new infrastructure
  (documented in ops runbooks, not yet written), not inserting a row.
- No schema, RBAC, or sync engine changes required right now — #17
  through #23's fixes stand as-is without an org-scoping re-audit.
- Cost per business stays higher than a shared model would allow;
  acceptable at current scale, worth re-costing if business count
  grows materially.
- Anyone proposing a new global-uniqueness constraint in the future
  (a new SKU-like field, a new numbering scheme) should be aware it's
  implicitly a single-tenant assumption — fine under this ADR, but
  worth a comment noting it if it'd need revisiting under Option B/C.
- Cross-business (multi-branch) reporting is out of scope until this
  ADR is revisited.

## Action Items

1. [ ] Add a short note to `app/models/` (or a README) stating the
       single-tenant-per-deployment assumption explicitly, so future
       contributors don't add `organization_id` piecemeal without
       revisiting this ADR.
2. [ ] If/when a concrete trigger from the Decision section occurs,
       open a new ADR (0002) re-evaluating Option B vs. C with real
       numbers (business count, hosting cost delta) instead of
       estimates.
3. [ ] No immediate code changes required.
