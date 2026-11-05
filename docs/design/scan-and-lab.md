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
 2 POST /uploads/presign ─────▶ signs a PUT URL (exact size and type)
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
  → {url, method:"PUT", headers, key, expires_in:600}   # presigned PUT: content-length + content-type are signed, so the store rejects any other body

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

---

## 11. As built: steps 1 and 2 (branch `feature/lab-and-storage`)

**Step 1, storage and visibility.** `POST /uploads/presign` (presigned PUT, exact size and type signed; see section 12), `POST /items/{id}/assets/register` (owner/manager), `GET /items/{id}/assets/{id}/download` (signed link). New settings `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` (AWS S3 or R2). `product_assets` gained `visibility` (default `staff`), `storage`, `scan_id`; existing rows were backfilled to `public`. The public API reads only `visibility='public'` server-disk files, and products gained `renders`. **Publishing copies the file from the private bucket to the public disk** (after the usual content checks) and only `render` and finished-piece `glb` can be published, so the private bucket never serves the website. A public `glb` is capped at 25 MB; larger models stay staff-only until a public CDN exists. Bucket CORS must allow GET from the shop address if carved part models are loaded in the Lab.

**Step 2, parts and Lab.** Differences from the sketch in §3 to §5:
- A part row is **one placed piece**. There is no `quantity`: add four rows for four legs (the editor has Copy). The cutting list groups identical pieces.
- `axis` (`x`, `y` or `z`) replaces `rot_deg`: it says which way the piece's length runs. Positions are the piece's **lowest corner**. Axes: L is left-right (x), W is front-back (z), H is up (y).
- Added `PUT /lab/items/{id}/size` (standard overall size) and `GET /lab/items/{id}/definition` for the editor. Part editing endpoints accept the Lab cookie only with the `X-Lab-Client: 1` header, and the cookie is `SameSite=Lax`.
- No ETag (not needed at this size). Responses are gzipped (the viewer scripts are large).
- The viewer is `app/static/lab/view.html` + `view.js`, the editor `edit.html` + `edit.js`, with Three.js r0.186.1 vendored (minified, MIT licence kept) under `app/static/lab/vendor/`. Pages use a strict content-security policy (own scripts only).
- Every save validates every formula against the standard size and every preset, and is refused with the part, field and size if any fails. Viewing is read-only.

## 12. As built: step 3, scan jobs (branch `feature/scan-jobs`)

Migration `e1f2a3b4c5d6` (additive): table `scan_jobs`, FK from `product_assets.scan_id`. API in `app/api/scans.py`.

Differences from the sketch in section 4:
- `POST /scans/{id}/presign` takes a **list** of files (one call per capture, not one per photo). Photos: JPG/PNG, 8 to 300 files, 25 MB each; video: one MP4/MOV, 2 GB.
- Every worker call (`progress`, `download-urls`, `results/presign`, `finish`, `fail`) carries `worker` (a name such as `workshop-pc`). The claim is stored as `user id | worker`, so two PCs sharing the owner login still cannot touch each other's job.
- Results are uploaded with `POST /scans/{id}/results/presign` to `scans/{id}/out/...`; `finish` checks every object exists **before** attaching any, then reuses `asset_service.attach_private_asset` (the same code as `/items/{id}/assets/register`). Results are staff-only unless a `render` or `glb` is published.
- `POST /scans/{id}/cancel` (creator or owner/manager, while `created` or `uploaded`) deletes the source files.
- Staff see only their own scans; owner/manager see all. `GET /scans?status=uploaded` lists oldest first (the queue).
- A lapsed lease can be re-claimed; after the third attempt the job becomes `failed` ("gave up after 3 attempts").
- Not built: a retry button for failed jobs, an "old jobs" warning screen.

**Correction (found while preparing the beta): presigned PUT, not POST.** Supabase Storage's S3 API implements `PutObject` but not browser-style `PostObject` policies (checked against its S3 compatibility page), and Cloudflare R2 is in the same position. Every presign endpoint (`/uploads/presign`, `/scans/{id}/presign`, `/scans/{id}/results/presign`) therefore returns `{key, url, method: "PUT", headers}`: the client sends the file as the request body with those headers. `Content-Length` and `Content-Type` are part of the signature, so the store still rejects a different size or type. Works on AWS S3, R2 and Supabase. Bucket lifecycle rules do not exist on Supabase, so the 60-day source retention needs a small scheduled job there (not built).

## 13. As built: joints and hardware (branch `claude/lab-joints-hardware-35cfa2`)

Migration `a4d7c1e9b2f6` (additive, has a downgrade): `furniture_parts.joint_type` (nullable text) and table `furniture_hardware`. Every API change is additive; existing fields and calls behave as before.

### Joints
Each part has an optional `joint_type`, one of:

| Value | Shown as |
|---|---|
| `butt_glue` | Butt joint, glued |
| `mortise_tenon` | Mortise and tenon |
| `dowel` | Dowel joint |
| `half_lap` | Half-lap |
| `dovetail` | Dovetail |
| `pocket_screw` | Pocket screw |
| `biscuit` | Biscuit joint |
| `other` | Other (use the note) |

Anything else is refused (422). `joinery_note` stays for free text ("tenon 10 mm, wedged"). Clients that never send `joint_type` keep working: it is simply empty. The viewer shows it in "Selected part".

### Hardware
`furniture_hardware` holds one line per kind of fastener for a furniture item: `kind`, `name` (for example "Wood screw 4 x 50 mm"), `qty_expr`, optional `part_id` and `assembly_step`, `note`, `sort`. Kinds: `screw`, `nail`, `dowel`, `bolt`, `bracket`, `glue`, `clamp`, `other`.

- **Quantity is a formula** of L, W, H, the same language as part sizes (no new functions). The solved quantity is rounded **up** to a whole number (`4.5` gives `5`, an exact `4.0` stays `4`).
- **Validated on save** for the standard size and every preset: it must be valid and give **0 or more**. The 422 names the item, the line and the size, for example `Dining Chair: hardware line 2, for size S: Oval nail: quantity formula 'L - 420' gives -20, it must be 0 or more`. Changing the standard size or the presets re-checks the hardware as well as the parts.
- **Step**: a line uses its own `assembly_step`; with none, the step of its `part_id`; with neither, no step.
- **Clamps are tools, not stock.** Lines of kind `clamp` are never in `hardware`; they come back in `tools`, grouped by build step ("hold with"), steps with none last. They are in no volume or cost total (the cutting list is unchanged by hardware). A line whose quantity solves to 0 is left out of the solved lists but stays in the definition.

### API
```
GET /lab/items/{id}              (workshop roles)   adds: parts[].joint_type,
                                  hardware: [{kind, items:[{id, kind, name, qty, step, part_id, note}]}],
                                  tools:    [{step|null, items:[...same...]}]
GET /lab/items/{id}/definition   (owner, manager)   adds: parts[].joint_type,
                                  hardware: [{id, kind, name, qty_expr, part_id, assembly_step, note}]
PUT /lab/items/{id}/parts        accepts joint_type, and an optional id per part (see below)
PUT /lab/items/{id}/hardware     (owner, manager)   {"hardware": [HardwareIn...]}, at most 100 lines
                                  replaces the whole list, returns the solved item
```
`PUT .../hardware` follows the same rules as parts: owner or manager, and the Lab cookie needs the `X-Lab-Client: 1` header. `definition.hardware` is the full editable list **including clamps** (kind `clamp`), because it is what the PUT replaces; the split into `hardware` and `tools` happens only in the solved output.

**Linking to a part and saving parts.** `PUT .../parts` replaces the list, so parts normally get new ids and the database would clear every `part_id`. A part sent **with its `id`** (the editor does; the id must belong to this item) keeps that id, and hardware stays linked. A client that sends no ids (desktop and phone today) still works, but its parts are new, so links are cleared and the lines stay as unlinked hardware.

### Editor and viewer
The editor has a joint dropdown per part row and a Hardware table (kind, name, quantity formula, step, note) with Add, Copy and Delete; the server's 422 text is shown as-is. The viewer shows the joint in "Selected part", a "Hardware" list under the cutting list (quantity × name, grouped by kind) and a "Hold with" list per build step. Both are in the printed page.

### Worked example: a queen bed
Overall size L 2100 (head to foot), W 1600, H 1000. Parts: four posts, head rail (`dowel`), side ledgers (`pocket_screw`).

| Kind | Name | Quantity formula | Step | Standard (2100 × 1600) | Small (2000) |
|---|---|---|---|---|---|
| screw | Wood screw 4 x 50 mm | `4 * (L - 160) / 200` (4 per slat ledger, a slat every 200 mm) | 2 | 39 (38.8 up) | 37 (36.8 up) |
| dowel | Dowel 10 x 60 mm | `8` (2 per rail-to-post joint, 4 joints) | 2 | 8 | 8 |
| bracket | Bed rail bracket set (plates, bolt, nut) | `4` (one set per rail corner) | 2 | 4 | 4 |
| glue | Wood glue (PVA) | `1` | 2 | 1 | 1 |
| clamp | Bar clamp 1200 mm | `4` | 2 | tools, step 2: 4 | 4 |
| clamp | F-clamp 300 mm | `4` | 1 | tools, step 1: 4 | 4 |

Bolt-on bed fittings (the gold-plated rail brackets with hex bolts, often sold as "bed clamps") stay in the bed, so they are kind `bracket` (or `bolt`) and are counted as stock. Kind `clamp` is only for workshop clamps that are taken off again.

The viewer then shows "Screws: 39 × Wood screw 4 x 50 mm", "Dowels: 8 × ...", "Brackets: 4 × ...", "Glue: 1 × ...", and under "Hold with": step 1 four F-clamps, step 2 four bar clamps. The same example runs as a test (`test_a_bed_with_ledger_screws_dowels_and_clamps`); nothing is written to any real database.

## 14. As built: carvings (designs on furniture parts)

Migration `b5e8d2f0c3a7` (additive, has a downgrade, follows `a4d7c1e9b2f6`): `designs.category`, `designs.image_key` / `image_type` / `image_sha256`, and table `furniture_carvings`.

### The idea: a design is stored once, placed many times
A carving is a **design from CNC designs placed on one face of a furniture part**. The design (code `DES-0007`, its name, its picture) exists once in `designs`. Every place it is carved is one small row in `furniture_carvings` (part, face, margin, mirror, note). The same lotus on both door panels is **one design and two placements**, and the Lab lists it once as "carved in 2 places".

The same picture also cannot be stored twice: `designs.image_sha256` is unique, so uploading identical bytes to a second design is refused (409) and names the first one, "use that design instead".

### Designs library
- `designs.category`: `general` (what every existing design is) or `furniture`. `GET /designs?category=furniture` filters. A design placed on a part in the Lab is moved from `general` to `furniture` automatically.
- `PUT /designs/{id}/image` (owner, artisan; multipart `file`): the design's picture, JPG, PNG or WebP, 10 MB, checked as a real image. It is **private**: kept under `private/designs/...` in the object store (the server's disk in development) and refused by `/media`. `GET /designs/{id}/image` returns it to owner and artisan.
- `GET /designs/{id}` adds `furniture`: where the design is carved (item, part, face). `DesignOut` adds `category` and `has_image`. Existing fields are unchanged.

### Carvings in the Lab
```
PUT /lab/items/{id}/carvings   (owner, manager)  {"carvings": [{part_id, design_id, face, margin_mm, mirror, note}]}
                               replaces the whole list (at most 60), returns the solved item
GET /lab/items/{id}            adds carvings: [{id, part_id, part, design_id, sku, name, face, margin_mm, mirror, note,
                                                area_mm:{w,h}, image_url}]
                               and designs:   [{id, sku, name, placements, image_url}]   (each design once)
GET /lab/items/{id}/definition adds carvings (raw), designs (the library an editor may choose from: id, sku, name,
                               category, has_image) and sort on each part
GET /lab/designs/{id}/image    (everyone who can view the Lab) the picture, but only of a design that is carved
                               somewhere; the rest of the library stays owner and artisan only
```
- `face` is a face of the part's box in the assembled piece: `left` (x = 0 side), `right`, `front` (z = 0 side), `back`, `top`, `bottom`. The picture is centred on the face and scaled to fit inside `margin_mm` of its edge, keeping its proportions. `area_mm` is that room, so the CNC operator knows the size to carve. It follows the size (Small, Medium, Large, custom).
- `mirror` flips the picture left to right, for the other side.
- A design must have a picture before it can be placed (422 names it). The margin must leave room at the standard size and every preset (422 names the item, line and size); changing the size or the presets re-checks it.
- **Part links:** like hardware, a carving points at a part by id. A part sent with its `id` when parts are saved keeps its carvings (the editor does this). A client that sends no ids still works, but its parts are new: the carvings stay in the list with no part, are not drawn, and can be pointed at a part again in the editor.
- Managers can edit the Lab, so the editor shows them the code and name of every non-retired design. They still cannot open the designs library itself.

### Editor and viewer
The editor has a Carvings table (part, design, face, margin, mirror, note; Add, Copy, Delete). To carve the second panel: Copy the row, change the part, tick Mirror. Save the parts first; only saved parts can be picked, and saving parts reads their ids back so nothing is lost. The viewer draws each picture on its face (fetched once per design and shared by every place), shows "Carving" in the Selected part panel, and has a "Carvings" list under "Hold with" with a thumbnail per design and each place it is carved with its size. It prints with the rest.

Not built: creating a design or uploading its picture from inside the Lab (use CNC designs), picture offset or rotation on the face, carving depth in the 3D view (the picture is flat), and the desktop app's designs screen does not show the picture or category yet.

## 15. As built: a scan becomes a furniture design, on request

Migration `c6f9e3a1d8b4` (additive, has a downgrade, follows `b5e8d2f0c3a7`): `designs.scan_id` (nullable, ON DELETE SET NULL).

The idea: furniture carries carved designs. Scan a carving, turn it into a **furniture design** (its depth picture, and the G-code if the scan made it), place it on the 3D model in the Lab (section 14), and keep it in CNC designs for future cutting. **It only happens when someone asks**, so test scans do not fill the designs library.

- `POST /designs/from-scan` `{scan_id, name?}` (owner, artisan; an artisan sees only their own product scans, as everywhere). The scan must be finished and be a **flat carving** (`params.shape = relief`) with a depth picture (the `render` file the worker uploads). The depth picture becomes the design's picture (same checks as any design picture), the design is filed under `furniture`, named `<piece> carving` unless `name` is given, linked to the piece that was scanned (`design_products`), and keeps `scan_id`.
- **Never a copy:** asking again for the same scan, or for a scan whose picture is already stored under another design, returns that design with `created: false`.
- `GET /designs/{id}` adds `scan_files`: the scan's results (depth picture, G-code, 3D files) with a `download_path` for each, which is where a future CNC print gets its files. `DesignOut` adds `scan_id`. For a restoration scan the existing `files` list already shows them.
- **In the Lab editor**, a "Designs from scans" section lists the finished flat-carving scans of the piece (newest first, only those with a depth picture) with a **Make a design** button and an optional name. The new design is offered in Carvings at once. `GET .../definition` adds `scans`; `POST /lab/scans/{id}/design` is owner only (designs are owner and artisan only, and artisans cannot open the editor), and a cookie write needs `X-Lab-Client: 1`.
- No change to the scan worker (`vriksha-scan`): it already uploads the depth picture and the G-code.

**Not built yet** (next steps, in this order): showing the design on the made-to-order production job, so the carver is told which design to carve and gets its files; and finding and cropping the carved design out of a photo of a whole bed with an image model, instead of scanning the carving by itself.

## 16. As built: mark a carved area on a photo (Step 3a)

No migration: a marked area is an ordinary flat-carving scan (section 15) whose `params` carry the marks. It needs the worker change in `vriksha-scan` (PR "Flat carving: read one marked area of a photo").

The problem: furniture photos are slanted and cluttered, and one bed has many separate designs (a border, a lattice panel, a medallion, a shaped crown). So the owner marks **one area at a time** on a photo, and each area becomes its own design.

- **Editor** ("Mark a carved area on a photo"): pick a JPG or PNG photo (25 MB at most), click the four corners clockwise from the top-left (or type them in the table, for keyboard use), optionally click points around a non-rectangular shape (an outline, for a crown), name it, enter the real width (and height) in mm, and send. Corners are in the pixels of the photo as it looks on screen (a phone's sideways turn is applied).
- `POST /lab/items/{id}/areas` (owner, manager; multipart: `photo`, `quad`, `outline`, `label`, `real_width_mm`, `real_height_mm`, `gcode`; cookie writes need `X-Lab-Client: 1`) checks the photo is a real JPG or PNG, the corners are clockwise, four-sided, uncrossed, inside the photo, and not tiny, then stores the photo privately and queues a scan (`status uploaded`, `params.shape = relief`, `params.crop = {quad, outline, real_height_mm}`, `params.label`). 503 if file storage is not set up.
- `GET /lab/items/{id}/scans` and `definition.scans` list the item's flat-carving scans with `status`, `progress`, `note`, `error`, `label` and `area`; the editor refreshes them every 10 s while one is waiting or being read. A finished one gets the **Make a design** button from section 15 (the label becomes the design's name).
- **Worker** (`relief.rectify_area`, `flatten`): straightens the four corners into a rectangle (a slanted photo reads as if straight-on), enlarges a small area to at least 1500 px (the depth model blurs a small repeating pattern: a 600 px tufted lattice came out as a blur and is crisp at 1500 px), reads the depth, takes out the overall slope a slanted photo adds, and, with an outline, makes everything outside it background and transparent in the picture, so a shaped design keeps its shape when it is placed on a part. The shape of the area is the real width and height if both are given, else the shape of the corners.
- **Which account runs the worker:** furniture scans are processed by an owner or manager account (an artisan account only processes restoration and sculpture scans).

Checked on real photos of slanted bed headboards: the border, the lattice and the footboard medallion come out flat, in proportion, and with the relief readable. The depth is still a model's estimate (relative, front face only); check it in millimetres on scrap before cutting.

**Not built yet:** finding the carved areas in a photo automatically (Step 3b), cancelling a marked area from the editor (use `POST /scans/{id}/cancel`), and showing the design on the made-to-order production job.

## 17. As built: templates and copying parts between pieces

No migration. Defining 25 to 33 parts by hand for every product is too much work, and the parts are formulas of the overall size (L, W, H), so one set fits every size of the same kind of piece. Two ways to start a piece:

- **Built-in templates** (`app/lab_templates/*.json`, shipped with the app and checked when loaded). Axes: L is left to right, W front to back, H up (for a bed, L is its length and W its width). Sizes are in mm; every template lists its typical sizes.

  | Template | Parts | Typical sizes (L x W x H) |
  |---|---|---|
  | `bed-square-posts`: square 4 inch posts, flat headboard (plain board, floral border, tufted lattice), low footboard with an oval medallion, rails, ledgers, centre beam, 12 slats. From the queen bed sheet. | 33 | queen 2185 x 1675 x 1270, king, double, single |
  | `bed-crown-headboard`: a shaped crown with a shell crest. From the king bed definition. | 25 | king 2185 x 1980 x 1300, queen, double, single |
  | `table-dining`: 40 mm top, four 80 mm legs, 25 mm aprons. H is the top's upper face. | 9 | 1200 x 750 x 740 up to 2100 x 1000 x 760 |
  | `chair-dining`: 450 mm seat, 40 mm legs, seat rails, top rail, carved back splat. | 12 | 450 x 480 x 950, small, large |
  | `wardrobe-2-door`: 25 mm carcass, 100 mm plinth, 10 mm back, centre divider, two shelves, a hanging rail, two 25 mm doors. W includes the doors. | 12 | 1000 x 550 x 1800 up to 1500 x 600 x 2100 |
  | `door-panelled`: two stiles, top, lock and bottom rails, an upper and a lower carved panel. W is the thickness. | 7 | 900 x 45 x 2100, narrow, wide |

  Each comes with the usual hardware (beds: bed rail bracket sets, which are stock; doors and wardrobes: hinges, handles, locks; all: screws, dowels, glue) and clamps as tools.
- **Copy from another piece**: the parts of any piece that has some, and optionally its hardware and carvings.

Either way the piece gets **its own copy** with new ids; the template or the other piece is never changed, and later edits do not flow back. Carvings keep their design (stored once) and hardware links move to the copied parts. A copied piece's 3D model file for a part is not carried over.

```
GET  /lab/templates                         (owner, manager)  id, name, description, size_hint, carve hints, part and hardware counts
POST /lab/items/{id}/apply-template         {template, wood_type?, hardware=true, replace=false}
POST /lab/items/{id}/copy-parts             {source_item_id, hardware=true, carvings=true, replace=false}
```
Both return the solved piece. The piece needs its standard size first (422 otherwise), and every formula must work for the standard size and every preset (422 names the part and the size, as for any save). A piece that already has parts, hardware or carvings is **refused with 409 unless `replace` is true**, and the editor asks before sending it. `wood_type` goes on every part of a template.

In the editor a "Start from a template or another piece" section sits above the parts table: pick a template (it shows what it is and where to carve), enter the wood, press the button; or pick a piece to copy from. The page reloads to show the new parts.

**What a template is not:** an exact model. Every thickness and position is a standard estimate (for the square-post bed, from the pictures: the sheet gives only L, W, H, the footboard height and the mattress space): check them with the achari. Only the six kinds above exist; a new kind of piece needs its template written once. At its sheet size the square-post bed reproduces the sheet's numbers: head post 1270, footboard 530, mattress space 1981 x 1524 mm (a queen mattress, 78 x 60 inches). **Every template is tested at each of its typical sizes: the parts fill exactly the overall box, no size is zero or negative, and no two parts overlap.** The slat count is fixed at 12; a longer bed spaces them wider. To add a kind, add a JSON file next to the others (see an existing one for the fields); the tests then check it automatically.

Carved areas are separate thin parts in the templates (each border strip, the lattice panel, the medallion), because a design is placed centred on one face of one part. Mark each area on a photo (section 16) and place its design on the matching part.
