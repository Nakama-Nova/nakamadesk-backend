# Scan pipeline and 3D Lab: system design

**Status:** design v2, 2026-10-26. Extends `docs/SYSTEM_DESIGN.md`; its §0 owner answers (budget, no GST yet, no payment gateway) still win. Nothing here is built.

---

## 1. Requirements

### Functional
| # | Requirement | Source |
|---|---|---|
| F1 | Staff capture a piece with the phone (video or 30 to 80 photos) in the Flutter app and upload it | owner |
| F2 | A GPU PC running the existing PyQt desktop app ("Scan Studio") downloads a scan, reconstructs it, and produces `.ply .obj .stl .glb` and CNC G-code | owner |
| F3 | Results are attached to the right product, each with a **visibility** (`public` or `staff`) | owner |
| F4 | The website shows the finished piece only: photos, a render image, the 3D model of the whole piece. **Parts, assembly, scans, `.nc` are never public** | owner |
| F5 | A separate, **read-only 3D Lab**, for **furniture only**: every part, exploded assembly, build order, size, material, cutting list | owner |
| F6 | In the Lab the achari picks **Small / Medium / Large** or types **custom L × W × H**; parts and the cutting list recalculate. **Nothing is saved** | owner |
| F7 | Only **owner and manager** edit part definitions and size presets | assumption, confirm |

### Non-functional
| Quality | Target |
|---|---|
| Cost | Whole system inside **~₹1,000/month** (server ~₹500–800 already). Storage must not add more than ~₹200 |
| Scale | One shop: ≤ 5 staff, ≤ 10 scans/week, ≤ 100 furniture items, ≤ ~40 parts each |
| Lab latency | p95 < 300 ms for a recalculation on the small server |
| Availability | The GPU PC can be off for days; scans just wait. The Lab and website must not depend on it |
| Security | Staff-only data must be unreachable from `/public/*`; the GPU PC and phones never hold cloud keys; a Lab link must not work as a login |
| Evolution | Additive migrations only (old desktop installs keep working) |

### Constraints
Single small FastAPI + Postgres server (1 to 2 GB RAM). Existing: backend, PyQt desktop, storefront (Next.js, public repo), `VrikshaStudio` CNC CLI. Flutter app is not built yet (it is the next client after this).

---

## 2. High-level design

```
 Phone (Flutter)                Shop server (FastAPI + Postgres)             GPU PC (desktop app)
 ───────────────                ───────────────────────────────             ────────────────────
 1 POST /scans ───────────────▶ scan_jobs row (status=created)
 2 POST /uploads/presign ─────▶ signs S3 POST policy (size/type capped)
 3 PUT files directly ─────────────────────────────▶ OBJECT STORE (private)
 4 POST /scans/{id}/complete ─▶ HEAD each key; status=uploaded
                                                                          5 GET /scans?status=uploaded (poll)
                                                                          6 POST /scans/{id}/claim  (lease)
                                                                          7 GET /scans/{id}/download-urls ◀── presigned GET
                                                                          8 reconstruct on GPU, export formats,
                                                                            optional G-code via vriksha.build()
                                                                          9 POST /uploads/presign, PUT results ─▶ OBJECT STORE
                                ◀──────────────────────────────────────── 10 POST /scans/{id}/finish (assets + visibility)
                                product_assets rows attached to the item

 Website visitor ─▶ Storefront ─▶ GET /public/catalog ─▶ only visibility='public' assets
 Achari / staff ─▶ POST /lab/links ─▶ short-lived link ─▶ browser opens /lab/view  (Three.js, vendored)
                                   └▶ GET /lab/items/{id}?size=M|?l=&w=&h=  (formulas solved on the server)
```

**Choices that shape everything**
1. **Object storage is a drop-box, not a compute service.** All heavy work is on the owner's PC, so there is no GPU rental, no quota request, no queue service. The "queue" is a table plus claim/finish endpoints.
2. **The big files never pass through the shop server.** It signs URLs and stores rows. This is what makes a 1 to 2 GB server enough.
3. **Storage is S3-compatible behind one module.** AWS S3 or Cloudflare R2 are the same code (`endpoint_url`). See §6 for the budget reason to prefer R2.
4. **One Lab viewer, served by the backend**, opened in a browser from the desktop app and in a web view from Flutter. No PyQt WebEngine dependency, nothing to keep in sync across three apps.

---

## 3. Data model (all additive; one Alembic migration per step)

### `product_assets` (exists) gains
| Column | Notes |
|---|---|
| `visibility` | `'public'` \| `'staff'`, NOT NULL. **Default `'staff'`** (safe by default); the existing photo and `.glb` upload endpoints set `'public'` explicitly. Migration backfills existing rows to `'public'` (they are today's public catalogue) |
| `kind` | add `render`, `stl`, `obj`, `ply`, `nc`, `source_scan`, `part_model` (today: `image`, `glb`) |
| `storage` | `'local'` \| `'s3'`. Public photos stay on the server disk today; heavy and staff-only files go to the object store |
| `scan_id` | nullable FK to the job that produced it |

### `scan_jobs`
| Column | Notes |
|---|---|
| `id`, `item_id` (nullable), `created_by` | item may be chosen at capture or when attaching |
| `status` | `created → uploaded → processing → done` or `failed`; `cancelled` |
| `source_kind`, `source_keys` (JSONB), `source_bytes` | `photos` or `video` |
| `params` (JSONB) | real width in mm, quality preset, G-code on/off |
| `claimed_by`, `lease_until`, `attempts` | lease so a dead PC releases the job |
| `progress` (0–100), `error` | shown in the Flutter and desktop apps |
| `result_asset_ids` (JSONB), `created_at`, `finished_at` | |

Index: `(status, created_at)`.

### Furniture parts (Lab data)
`furniture_parts`: `id`, `item_id`, `name`, `quantity`, `wood_type`, `raw_material_id` (nullable, ties to the existing BOM), **`l_expr`, `w_expr`, `t_expr`** (formulas), **`x_expr`, `y_expr`, `z_expr`** + `rot_deg` (placement in the assembled piece), `assembly_step` (int), `joinery_note`, `model_asset_id` (nullable `.glb` for a carved part), `sort`.
`items.size_presets` (JSONB): `{"S":{"l":..,"w":..,"h":..},"M":{..},"L":{..}}`. `items.dimensions_mm` stays the default size. `items.is_furniture` (bool, default false): the Lab lists only these.

### Formulas
Overall size `L W H` (mm) feeds every part: `Leg length = H - 30`, `Top = L × W × 30`, `Side rail = W - 100`. Allowed: numbers, `L W H`, `+ - * /`, parentheses, unary minus. Parsed with Python's `ast` and a node whitelist (no `eval`, no names, no calls, no `**`), length ≤ 100 chars, every result must be finite and in (0, 10 000] mm. **Validated at save time against every preset and the default size**, so a bad formula is rejected when the owner enters it, not when an achari opens the Lab.

---

## 4. API contracts

All new routes are additive. Roles use the existing `UserRole`.

**Uploads and scans**
```
POST /uploads/presign        (any staff)
  {purpose:"scan_source"|"scan_result"|"asset", scan_id?|item_id?, filename, content_type, size}
  → {url, fields, key, expires_in:600}        # S3 POST policy: content-length-range + content-type enforced by the store

POST /scans                  (any staff)   {item_id?, source_kind, params}      → {id}
POST /scans/{id}/complete    (creator)     {keys:[...]}   # server HEADs each key (exists, size ok) → status=uploaded
GET  /scans?status=uploaded  (owner, manager)                                   → [{id, item, kind, bytes, created_at}]
POST /scans/{id}/claim       (owner, manager) → 200 {lease_until} | 409 already taken
POST /scans/{id}/progress    (claimer)     {percent, note}     # also renews the lease
GET  /scans/{id}/download-urls (claimer)   → [{key, url}]      # presigned GET, 15 min
POST /scans/{id}/finish      (claimer)     {assets:[{key, kind, visibility, content_type, bytes}], item_id}
POST /scans/{id}/fail        (claimer)     {error}
```

**3D Lab (read-only)**
```
POST /lab/links              (owner, manager, achari, worker)  {item_id} → {url}   # opens the viewer
GET  /lab/items              → furniture list (id, name, presets available)
GET  /lab/items/{id}?size=M  or  ?l=1200&w=600&h=750
  → {size:{l,w,h}, parts:[{name, qty, l,w,t, x,y,z, rot, step, wood, note, model_url|null}],
     cutting_list:[{wood, items:[{name, qty, l,w,t, volume_m3}], total_m3}], steps:n}
```
Part editing (owner, manager only): `POST/PUT/DELETE /lab/items/{id}/parts`, `PUT /lab/items/{id}/presets`. The viewer never calls these.

**Public API (changed rule, same shapes):** every asset query in `public_service` filters `visibility='public'`. Customer pages get `images`, `renders` (new), and the finished-piece `model_url`.

---

## 5. Deep dive

### 5.1 Job queue (claim, lease, retry)
- **Claim:** `UPDATE scan_jobs SET status='processing', claimed_by=:pc, lease_until=now()+30min, attempts=attempts+1 WHERE id=:id AND (status='uploaded' OR (status='processing' AND lease_until<now())) RETURNING *`. One statement, so two PCs can never both win (the loser gets 409).
- **Lease:** `/progress` renews it. If the PC dies, the lease lapses and the job is claimable again. After **3 attempts** it becomes `failed` with the last error, visible in the apps.
- **Idempotent finish:** `/finish` with the same asset keys again returns the same result; assets are keyed by object key, so a retried call cannot create duplicates.
- **Completeness:** a job only becomes `uploaded` after `/complete` HEADs every object, so the PC never starts on half an upload.
- **Polling, not push:** the PC asks every 30 s while the app is open. No inbound connection to the owner's network, no extra service.

### 5.2 Storage
- Private bucket. Staff-only and large files are served only by **presigned GET (15 min)**. The server's access key is limited to this bucket; **phones and the GPU PC hold no cloud keys at all**.
- Keys: `scans/{scan_id}/src/{n}.{ext}`, `scans/{scan_id}/out/{name}`, `items/{item_id}/{kind}/{uuid}.{ext}`.
- Public photos and the finished-piece `.glb` remain on the server disk now (works today, free). When traffic justifies it, move them to a public bucket behind a CDN by changing `storage` and `media_service.url()` only.
- **Retention:** source scans are deleted 60 days after `done` (lifecycle rule); results are kept. This is what keeps storage cost flat (§6).

### 5.3 Scan Studio (desktop app on the GPU PC)
State machine per job: `idle → claimed → downloading → extracting frames (video) → reconstructing → cleaning mesh → exporting → generating G-code (optional) → uploading → finished`. Each step is a function with a progress callback; the work runs in a worker thread/subprocess so the window stays responsive; **cancel** kills the subprocess and releases the lease.
- **Reconstruction:** COLMAP (sparse + dense) and OpenMVS (mesh + texture), both CUDA. Binary paths are settings. ffmpeg picks sharp frames from video (variance of Laplacian, ~2 frames/second, cap 150).
- **Export:** `trimesh` writes `.ply .obj .stl .glb`. A 150 k-face mesh is a few MB per format.
- **G-code:** `VrikshaStudio/vriksha.py` already exposes `build(cfg)` and `parse(argv)`, so Scan Studio imports and calls it (no subprocess). It adds its own dependencies (numpy, scipy, trimesh, networkx, pillow) to the **desktop** requirements only.
- Failures are written to the job (`/fail`), never swallowed; a short log is kept in the working folder.
- **Verify before trusting:** every mesh gets a quick sanity check (watertight or hole count, bounding size vs the entered real width) and warnings are stored in `params`. A scan is a starting mesh, not a cutting file.

### 5.4 The Lab
- **Solve server-side:** `lab_service.solve(item, size)` evaluates every formula with the whitelist evaluator and returns plain numbers. The viewer and the printed cutting list therefore cannot disagree, and the viewer has no formula code at all.
- **Cutting list:** per part `qty × l × w × t`, grouped by wood, volume in m³ (net, no waste factor; a configurable allowance is a later setting).
- **Explode:** the viewer moves each part away from the assembly centre along `(part centre − centre)`, scaled by a slider; no stored vectors. **Steps:** parts are shown by `assembly_step`; the current step is highlighted. A part with a `model_asset_id` loads that `.glb` scaled to its resolved box; otherwise it is a box.
- **Viewer delivery:** static HTML + JS in `app/static/lab/` with **Three.js vendored** (no CDN; works on the shop LAN and offline of third parties). 
- **Link, not login:** `POST /lab/links` returns a link with a signed token, **scope `lab`, 10 minutes**. `/lab/open` exchanges it for an **HttpOnly cookie** and redirects to a clean URL. **`get_current_user` rejects any token that has a `scope` claim**, so a Lab link can never call the rest of the API. Responses send `Referrer-Policy: no-referrer`.
- **Cache:** none needed (≤ 40 parts, microseconds of arithmetic). Responses carry an ETag from item `version` so reopening is free.

### 5.5 Errors and edge cases
| Case | Behaviour |
|---|---|
| Upload dropped on mobile data | Presigned POST is single shot: the app retries the whole file (up to 3 times). Multipart/resumable only if this proves painful (§9) |
| `/complete` but an object is missing | 409 with the missing keys; job stays `created` |
| GPU PC off | Jobs wait. Apps show "waiting for the workshop PC" and the age of the oldest job |
| Reconstruction fails (not enough overlap, shiny surface) | `/fail` with a plain reason and capture advice; the phone app shows it |
| Formula divides by zero or goes ≤ 0 for a custom size | 422 naming the part and the formula; the Lab shows it next to the size fields |
| Staff-only asset requested via public API | Not returned; covered by a test over every public endpoint |
| Same asset uploaded twice | De-duplicated by object key (and optional SHA-256) |

---

## 6. Scale, cost, reliability

**Load estimate.** 10 scans/week × ~300 MB (video 60–90 s or ~50 photos) ≈ **3 GB/week up**, the same down to the PC. Results ≈ 50 MB/scan. Lab: tens of calls a day. API calls for scans: a few hundred a day including polling. All trivial for Postgres and a 1 GB server.

**Storage growth and cost** (⚠️ estimates, check current prices; ~₹85 per USD)
| | No retention | Sources deleted after 60 days |
|---|---|---|
| Stored after 12 months | ~156 GB sources + 26 GB results | ~26 GB sources (steady) + 26 GB results |
| AWS S3 Mumbai (~$0.025/GB·month) | ~₹390/month and rising | ~₹110/month, flat-ish |
| Cloudflare R2 (10 GB free, ~$0.015/GB, **no egress fee**) | ~₹210/month | ~₹60/month |

Egress: the PC downloads ~13 GB/month. S3 normally charges for data out beyond a free allowance; R2 charges nothing. **Recommendation: start on Cloudflare R2** (same S3 code, no AWS account needed, nothing billed under the free tier at first); switch to AWS S3 by changing four environment variables if you prefer. Either way keep the 60-day source retention, or the storage line alone can exceed the budget by year two.

**Scaling.** Vertical only, and not needed. If scans grew 10×: nothing changes except the object-store bill and a second GPU PC (the claim query already allows several workers).

**Reliability and backups.**
- DB: the existing nightly `pg_dump`. **Add the server `media/` folder** (public photos) to the backup. The object store is the durable copy of scans/results: turn on versioning only for `items/` if desired.
- Single points of failure are accepted: one small server, one GPU PC. Both can be down without losing data (uploads queue, results are re-uploadable).
- Leases and idempotent finish give at-least-once processing without duplicates.

**Monitoring (kept tiny).** The `scan_jobs` table *is* the dashboard: a staff screen lists jobs with age and status, and highlights anything `uploaded` for more than 24 h or `processing` with an expired lease. Plus the existing health check and one uptime ping. No metrics stack.

---

## 7. Security
- Visibility is enforced in **one function** used by every public query; a test enumerates all `/public/*` routes with staff-only assets present and asserts none leak.
- Presigned POST policies cap size and content type at the store; the server re-validates after upload (`/complete`, and image/`.glb` magic checks where the file is small enough to read).
- The Lab link token is single-purpose (`scope`), short-lived, exchanged for an HttpOnly cookie, and rejected by `get_current_user`.
- **The storefront repo is public**: parts, scans and `.nc` only exist behind staff login and in the private store, never in that repo or its bundles.
- Roles: capture = any staff; claim/finish = owner, manager (the GPU PC logs in as one); Lab view = owner, manager, achari, worker; part editing = owner, manager.
- Uploads from phones are authenticated; presign is rate-limited per user.

---

## 8. Trade-offs

| Decision | Chosen | Alternative | Why / cost |
|---|---|---|---|
| Where 3D runs | Owner's GPU PC | Rented AWS GPU | No quota wait, no per-scan bill, but a PC to buy and keep on; the queue absorbs downtime |
| Queue | Postgres table + polling + lease | SQS / Redis | Zero new services; polling adds up to 30 s latency, irrelevant here |
| Object store | S3-compatible, R2 recommended | AWS S3 only | R2 is cheaper (no egress) and needs no AWS account; AWS is one config change away |
| File transfer | Presigned POST direct to the store | Proxy through the API | Keeps big files off the small server; no resume on failure (accepted for now) |
| Lab computation | Server solves formulas | Viewer solves in JS | One source of truth, safe evaluator in one place; one request per resize (cheap) |
| Part sizes | Formulas of L W H + S/M/L presets | Fixed tables per size; full parametric CAD | Covers the asked cases simply; cannot express joinery-driven geometry (revisit) |
| Lab client | One web viewer opened in a browser/web view | Native in PyQt and Flutter | Written once; needs a browser on the desktop and a web view in Flutter |
| Three.js | Vendored copy | CDN | No external dependency at the workbench; ~0.6 MB to maintain |
| Public photos | Server disk for now | CDN bucket | Free and working; moves later by changing `storage` |
| Visibility default | `staff` | `public` | A forgotten flag hides something instead of leaking it |

---

## 9. What I would revisit as it grows

- **Resumable uploads** (S3 multipart with presigned parts) if phone uploads of videos fail often on mobile data.
- **Public asset CDN** (R2 public bucket or CloudFront) when photos make the server's disk or bandwidth the bottleneck.
- **Real parametric CAD** (FreeCAD/OpenSCAD kernel) if parts need joinery-driven geometry, not just boxes scaled by formulas.
- **A waste/allowance factor** and stock check in the cutting list, linked to `raw_materials` and purchases.
- **Saving a Lab configuration as a variant on a custom order** (today it is view-only).
- **Several GPU PCs** (the claim query already supports it) or a cloud GPU worker if scan volume outgrows one PC.
- **Automatic quality checks** on meshes (hole count, scale) with a go/no-go before G-code is offered.
- **Customer renders:** turntable stills generated on the GPU PC (Blender), uploaded as `render`; later maybe automatic per product.

## 10. Build order (what `/plan` covers)

1. **Storage and visibility:** object-store module (S3-compatible), presign endpoint, new asset kinds, `visibility` + public filter + leak tests, retention rule documented.
2. **Parts and 3D Lab:** parts/presets/`is_furniture` model, safe formula evaluator, `/lab` API, scoped-token link, viewer, cutting list. Useful at once; needs no GPU or phone work.
3. Scan jobs + Flutter capture · 4. Scan Studio on the GPU PC · 5. Renders for the site (later plans).
