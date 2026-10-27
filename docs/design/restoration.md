# Temple restoration

The artisans' work: old, damaged temple carvings are photographed or filmed, a 3D
model is built on the workshop GPU PC, and the model (and CNC G-code for relief
panels) helps recarve or repair the piece.

## Who sees it

Only the **owner** and **artisans** (`RESTORATION_ROLES` in `app/db/deps.py`). A
manager, sales, achari or worker gets 403 on `/restorations` and 404 on a restoration
scan, as if it did not exist. Results are **private files of the project**
(`restoration_files`); they are never `product_assets`, so the website cannot show them.

## Data

- `restoration_projects`: title, temple, location, piece, condition notes, status, lead artisan.
  Status: planned, capturing, processing, review, carving, restored, on_hold, cancelled.
  It moves on by itself twice: upload complete -> `processing`, results in -> `review`.
- `scan_jobs.restoration_id`: the existing scan queue carries restoration scans.
- `restoration_files`: result kind (glb, stl, obj, ply, nc, render), private key, size.

## Flow

```
phone or desktop  POST /scans {restoration_id}  -> presign -> upload -> complete
workshop PC       claim -> download-urls -> reconstruct -> results/presign + upload -> finish
                  (the PC signs in as the owner or an artisan account)
anyone allowed    GET /restorations/{id}  ->  files  ->  GET .../files/{id}/download (signed link)
```

## Capture limits

Photos: up to 100 MB each, up to 300 per scan (at least 8). Video: one file up to 1 GB.
The object store enforces the size; check that the bucket itself allows files this large
(Supabase Storage's free plan caps a file at 50 MB; Cloudflare R2 has no such cap).
