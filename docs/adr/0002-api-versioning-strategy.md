# ADR-0002: API Versioning Strategy

**Status:** Proposed
**Date:** 2026-09-29
**Deciders:** Product owner, backend tech lead

## Context

The master engineering prompt (§9, API Architecture) says to "use API
versioning where appropriate." Today there is none: every endpoint is
unversioned (`/items`, `/sales`, `/sync/push`, `/auth/login`, ...) —
confirmed by `app/main.py`'s router registration and every
`APIRouter(prefix=...)` declaration in `app/api/`.

Unlike ADR-0001's multi-tenancy question, this one has a real,
already-live trigger: multiple clients (desktop, and a growing
mobile app) consume this API from installs that don't upgrade in
lockstep with the backend. Per ADR-0001, deployment stays one backend
per business, but *within* one business there can be several desktop
POS terminals and, eventually, several phones — all pointing at one
backend URL, none forced to update the instant the backend redeploys.
A breaking API change today would break whichever terminal hasn't
restarted yet, mid-rollout, with no warning and no escape hatch.

The concrete blast radius of adding versioning now, measured rather
than assumed:
- **Backend:** mechanical — each `app.include_router(...)` call in
  `app/main.py` would need a `prefix="/api/v1"`, a ~16-line change.
- **nakama-desktop:** `services/api_client.py` hardcodes 21 separate
  bare-path endpoint strings (`/items`, `/sync/push`, `/auth/login`,
  etc.) that would all need the new prefix.
- **furnibiz-android:** `ApiService.kt` only has **one** endpoint
  wired so far (`auth/login`) — the app is still early-stage (issue
  tracker confirms only auth + dashboard exist).
- **backend tests:** dozens of test files call bare paths directly
  (`auth_client.post("/items/", ...)` and similar), each of which
  would need updating.
- The sync protocol (`/sync/push`, `/sync/pull`) is the highest-risk
  surface specifically: its payload schemas have already changed
  substantively tonight (#18, #20 made fields optional, added
  `RawMaterialPayload`) with no version signal for a client to detect
  it's talking to a backend that expects a different shape.

So: real risk exists (client/server version skew), but the "obvious"
fix (URL-path versioning, `/api/v1/...`) has a real, non-trivial cost
today for zero present benefit — there is no v2 to serve yet, so
versioning the URL buys nothing except a rename.

## Decision

**Do not adopt URL-path versioning (`/api/v1/`) yet.** Instead, adopt
two cheap, low-blast-radius practices now, and treat the URL-prefix
migration as the thing to do *at the moment* a genuine breaking change
is needed — not before.

1. **Additive-only schema evolution is now explicit policy**, not just
   an accident of how tonight's fixes happened to be shaped. New
   fields on request/response schemas must be optional with sensible
   defaults; removing or renaming a field, or changing its type, is a
   breaking change and requires this ADR to be revisited first.
2. **Add a version signal to the sync protocol specifically** — the
   one stateful, schema-sensitive surface where a silent mismatch is
   worst. A `sync_protocol_version` field in `SyncPushResponse`/
   `SyncPullResponse` (or a dedicated field on `/health`) that clients
   can check, so a client running against an incompatible backend
   gets a clear "update required" signal instead of a confusing
   422/500. This does not require touching any existing URL.

## Options Considered

### Option A: URL-path versioning now (`/api/v1/...`)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low on the backend (mechanical prefix change); real elsewhere |
| Cost | ~16-line backend change, but rewrites 21 client call sites in desktop, every backend test call site, and (small, since android barely started) android — for a v2 that doesn't exist yet |
| Scalability | Doesn't matter yet — there's only one version to serve |
| Team familiarity | Standard REST pattern, well understood |

**Pros:** future-proofs URL structure; standard, unsurprising pattern;
easiest to do *now* while client/test counts are still small.

**Cons:** all cost, no present benefit — there is nothing to version
against. Touches every client and test file for a rename, not a
capability.

### Option B: Additive-only discipline + sync version signal (chosen)

No URL change. Formalize non-breaking-by-default schema evolution;
add a lightweight version field to the one protocol (sync) where
mismatch risk is real and silent failure is worst.

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — a policy + a few new response fields |
| Cost | Near zero; no existing call site changes |
| Scalability | Buys time until a real v2 is needed; doesn't solve versioning forever |
| Team familiarity | Requires discipline (code review catching breaking changes), not tooling |

**Pros:** costs almost nothing today; targets the actual live risk
(sync schema drift) directly instead of versioning everything
uniformly; doesn't force a rename of 21+ call sites for no capability
gain; matches the master prompt's own "avoid unnecessary complexity"
guidance (§34) the same way ADR-0001 did.

**Cons:** relies on code-review discipline rather than a structural
guarantee; a genuinely breaking change (which will eventually be
needed) still requires the Option A migration at that point — this
defers the cost, it doesn't eliminate it.

### Option C: Header/content-negotiation versioning (`Accept: vnd.nakama.v2+json`)

| Dimension | Assessment |
|-----------|------------|
| Complexity | High — every client needs header-aware request building |
| Cost | Highest of the three; desktop and android would both need new request plumbing |
| Scalability | Technically the most "correct" REST approach |
| Team familiarity | Low — not used elsewhere in this stack |

**Pros:** avoids URL churn entirely for future versions.

**Cons:** most expensive to implement across three codebases for a
team of this size; no evidence this level of rigor is needed yet.
Rejected as over-engineering for the current stage.

## Trade-off Analysis

The deciding factor is the same as ADR-0001: cost now vs. benefit now.
Option A's cost is concrete and immediate (21+ call sites, every test
file); its benefit is zero until a v2 actually exists to route to.
Option C is strictly more expensive than A for the same non-existent
benefit. Option B is the only one that spends effort proportional to
the *actual* live risk today — sync payload drift between a backend
that's still actively evolving its schemas and clients that don't
upgrade instantly — without pre-paying for a capability (parallel API
versions) nothing currently needs.

This is not "never version the API" — it's "don't pay for versioning
before there's a second version." When a genuine breaking change is
needed, Option A's migration will still be small (client/test counts
are low right now, especially android's single wired endpoint), so
deferring doesn't make it harder later — arguably easier, since it can
be done once, deliberately, alongside the breaking change itself,
rather than speculatively now.

## Consequences

- Code review must treat "does this change break existing clients"
  as an explicit checklist item on every schema/endpoint change,
  since there's no version boundary catching it structurally.
- `SyncPushResponse`/`SyncPullResponse` gain a `sync_protocol_version`
  field; desktop's `SyncService` should check it and log/warn on
  mismatch rather than fail silently.
- The first genuinely breaking API change (removing a field, changing
  a type, restructuring a payload) is the trigger to introduce
  `/api/v1/` — at that point both the old and new shapes exist
  simultaneously for the migration window, which requires the prefix.
- furnibiz-android, being least built-out, is the cheapest client to
  keep versioning-ready — worth wiring its (currently single)
  endpoint with this policy in mind from the start rather than
  retrofitting later.

## Action Items

1. [ ] Add `sync_protocol_version` to `SyncPushResponse` and
       `SyncPullResponse` (`app/schemas/sync.py`), and have desktop's
       `SyncService` log a warning on an unexpected value.
2. [ ] Document the additive-only schema policy in
       `nakamadesk-backend`'s README or `CONTRIBUTING` notes, so it's
       enforced in review, not just tribal knowledge.
3. [ ] No URL changes now. Revisit as Option A the moment a genuinely
       breaking change is needed, not before.
