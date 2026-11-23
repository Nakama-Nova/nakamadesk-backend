# Dynamic roles & permissions ("Nakama IAM") — design

Status: built. Every route now asks for a permission with `require(...)`; the old `check_role` is gone. Sections 1-3 describe the starting point and the plan. Still open: roles screens in the desktop app, plant-scoped checks on individual routes, and section 4.

Status before: proposal. Scope: `nakamadesk-backend` (source of truth) + clients
(`nakama-desktop*`, `vriksha-mobile*`, `vriksha-storefront*`). Other repos are
forks/worktrees of the same backend or desktop and inherit whatever lands in main.

## 1. What we have today (inventory)

**Roles are a hardcoded enum** — `app/models/enums.py::UserRole`:
owner, manager, sales, achari, worker, artisan, accountant. `users.role` is a plain string.

**Enforcement = `check_role([...])` lists copy-pasted per endpoint** (41 files in `app/`):

| Module | Files | Who today |
|---|---|---|
| Attendance | api/attendance.py, models/attendance.py, services/workforce_service.py | owner/manager/sales mark+view; owner/manager edit |
| People / staff CRM | api/auth.py (user CRUD), api/team.py, services/team_service.py, staff_code_service | owner only manages users; reports_to_id tree; anyone reads tree |
| Salary & wages | api/payroll.py, api/wages.py, models/payroll.py, daily_wage.py | `PAYMASTERS` = owner/manager; self-view fallback |
| Sales | api/sales.py, orders.py, invoices.py, customers.py, letters.py | owner/manager/sales; owner-only deletes |
| Purchases | api/purchases.py, raw_materials.py, suppliers (masters.py) | owner/manager (+sales on some reads) |
| Finance ledger | finance/api.py | READ owner/manager/accountant; BALANCE_SHEET owner/accountant; WRITE owner |
| Production / inventory | production.py, bom.py, items.py, machines.py, tools.py, stock_locations.py | owner/manager/achari |
| Restoration + 3D Lab + scans | restorations.py, designs.py, lab.py, scans.py | owner/artisan; lab roles list |
| Site / CMS, reports, budgets, dashboard, expenses | site.py, reports.py, budgets.py, dashboard.py, expenses.py | owner/manager; budgets-set owner |

**Row-level rules buried in code** (these are the hard part): ARTISAN sees only own assets
(assets.py:34); cost fields hidden from non-COST_VIEWERS (items.py); letter kinds filtered by
role (`letter_service.ROLES`); payroll self-view; expenses restricted to restoration roles;
"cannot change own role", "last owner" guards (auth.py); manager-chain rule (team_service).

**Clients duplicate the enum**: mobile `session.dart` (`isOwner`, `canSell`, `canRestore`),
desktop `main_window.py` role→menu branches, `session.is_authorized`, staff role pickers.
Adding a role = edit backend + 2 clients + both pickers.

Good foundations to keep: `token_version` (kills sessions on role change), `audit_log`,
`reports_to_id` hierarchy, scoped Lab tokens.

## 2. Target model (AWS-IAM analogy)

| AWS | Nakama | Table |
|---|---|---|
| Action `s3:GetObject` | `service:resource:verb`, e.g. `payroll:payment:create` | code registry (not DB) |
| Policy (JSON, Allow/Deny, conditions) | same shape, DB-stored | `iam_policy` |
| Role | named bundle of policies (owner, manager… become *seed data*) | `iam_role` + `iam_role_policy` |
| User / Group | users keep one primary role + extra roles; optional groups later | `user_role` (M:N) |
| Resource ARN | `nakama:<module>:<type>/<id>` with scope tags (plant_unit, kind) | string in policy |
| Condition keys | `self`, `reports_to_me`, `plant_unit`, `amount_lte`, `field` | evaluator |
| Permission boundary | per-user cap, stops managers granting beyond themselves | `iam_user.boundary_policy_id` |

Policy document:

```json
{"version":1,"statements":[
  {"effect":"Allow","actions":["payroll:payment:*"],"resources":["*"]},
  {"effect":"Allow","actions":["payroll:payment:read"],"resources":["*"],"condition":{"self":true}},
  {"effect":"Deny","actions":["finance:*:write"],"resources":["*"]}
]}
```

Evaluation (same as IAM): default deny → any matching Deny wins → any matching Allow permits.
Wildcards on action segments. Union across all of the user's roles. `owner` role = `*:*:*`,
**not deletable/editable, ≥1 active holder** (preserves today's last-owner guard).

### Tables (Alembic migration, additive)

```
iam_action        (code PK, module, description)        -- synced from code registry at startup
iam_policy        (id, name unique, document JSONB, is_system, created_by, updated_at)
iam_role          (id, key unique, label, is_system, description)
iam_role_policy   (role_id, policy_id)
user_role         (user_id, role_id)                    -- M:N; users.role kept as legacy primary
iam_user_policy   (user_id, policy_id)                  -- optional direct attach
```

### Enforcement

```python
# replaces check_role([...])
def require(action: str, resource_fn=None):
    def dep(user=Depends(get_current_user), db=Depends(get_db)):
        if not iam.allowed(user, action, resource_fn and resource_fn(...), db):
            raise HTTPException(403, f"Missing permission: {action}")
        return user
    return dep
# usage:  Depends(require("payroll:payment:create"))
```

- `iam.allowed` loads the user's effective statements once per request (cache in
  process, key `(user_id, token_version)`; role/policy edit bumps `token_version` —
  already how sessions are invalidated).
- Row filters (artisan-own-assets, letter kinds, payroll self) become **conditions** or
  `iam.scope(user, action)` returning a query filter — no more `if role == ARTISAN`.
- Field-level (cost fields): `iam.can_field(user, "item:cost")`.
- `GET /auth/me/permissions` returns effective allowed action patterns → clients gate UI
  on **permissions, not role names** (`can('sales:order:create')`).

### Management API (owner or holders of `iam:*`)

`/iam/actions`, `/iam/policies` CRUD, `/iam/roles` CRUD + attach, `/iam/users/{id}/roles`,
`POST /iam/simulate {user, action, resource}` (IAM policy simulator — also our test oracle).
Every write → `audit_log`. Guards: can't remove own `iam:*`, can't grant above own boundary.

## 3. Migration plan (zero-downtime, reversible)

1. **Add tables + registry + `iam.allowed`**; seed system roles that reproduce the *current*
   behaviour exactly (owner/manager/sales/achari/worker/artisan/accountant) by translating each
   `check_role` list into policies.
2. **Parity test**: for every route × every legacy role, assert `old check_role` ==
   `iam.allowed` (generate from FastAPI route table). Block merge on any diff.
3. Swap `check_role([...])` → `require("...")` module by module (finance first — already
   centralised in 3 lists; payroll second). `check_role` stays as shim reading the seeded policies.
4. Ship `/auth/me/permissions`; mobile/desktop switch from role checks to `can()`;
   keep returning `role` for old app versions (mobile builds are in the field).
5. Add role/policy admin UI (desktop Staff screen → "Roles"), then drop `UserRole` enum
   from pickers (roles loaded from `/iam/roles`). DB `users.role` column stays a free string.
6. Remove shim once min client version ≥ step 4.

## 4. What else to make dynamic (beyond permissions)

Ranked by pain-per-effort:

1. **Salary & wages rules** — today `base_daily_wage` + `overtime_rate_per_hour` on user and
   `kind` ∈ piece|overtime|bonus hardcoded. Make: `pay_component` (earning/deduction, fixed|%|per-unit,
   taxable flag), `pay_structure` per role/grade/user with effective-from dates, advance-recovery
   rules, payment methods. Payroll run computes from components; history stays immutable.
2. **Attendance policy** — statuses, shift/hours, grace, geofence radius per plant unit, who may
   mark/override, auto-checkout, weekly-off/holiday calendar, leave types + balances.
3. **Approval workflows** — "amount > X needs owner" for purchases, wages, discounts, expense
   edits. Expressed as an IAM *condition* (`amount_lte`) + an `approval` table; one engine, many uses.
4. **People/HR master data** — departments, designations, grades, employment type, custom fields
   (EAV/JSONB) on staff; reports-to stays; staff-code format configurable.
5. **Sales & purchases** — order/PO status machines, document numbering (`invoice_counter`
   already close), tax/GST rates table, price lists/discount tiers per customer group,
   payment terms, letter templates + which role may issue each kind.
6. **Master lists as data** — item types, units, categories, expense categories, stock locations
   (some exist in `masters.py`; extend pattern, drop remaining enums).
7. **Notifications & feature flags** — who gets which alert; per-role module toggles
   (hide Restoration/Lab for tenants that don't use it).
8. **Multi-tenant / multi-unit** (later) — `tenant_id`/`plant_unit` as a scope tag in resources so
   one policy set serves several workshops. Don't build now; reserve the resource-ARN slot.

## 5. Trade-offs & decisions

- **JSON policies vs simple permission checklist**: chose policies for Deny + conditions
  (self, plant unit, amount). Cost: evaluator + simulator (~300 LOC). A flat role→permission
  table would be ~1 day shorter but can't express artisan-own / payroll-self / amount limits
  without leaving code `if`s behind — the exact problem today.
- **Actions in code, policies in DB**: prevents typos granting nothing; new endpoint must
  register its action (startup check fails on unregistered `require()`).
- **Keep `users.role`** as legacy/primary for now: old mobile builds and sync
  (`sync_service.py`, 34 role refs) depend on it; remove last.
- **Cache**: per-process LRU keyed by `token_version`; Vercel/Lambda cold starts mean no
  shared cache needed at this scale (<100 users).
- **Risk**: lock-out. Mitigation: owner role immutable, `iam:*` self-removal blocked,
  CLI break-glass `python -m app.iam grant-owner <username>`.

## 6. Open questions

1. Single workshop only, or multiple plant units/tenants with separate staff visibility?
2. Should managers be allowed to create roles (bounded by boundary) or owner only?
3. Statutory payroll needs (PF/ESI/TDS) now, or just advances + piece + overtime?
4. Minimum supported mobile/desktop versions (decides when the shim can go)?

## 7. Suggested first PR (small)

Tables + registry + `iam.allowed` + seeded roles + parity test + finance module converted
(it is already the cleanest: 3 role lists). No client changes, no behaviour change.
