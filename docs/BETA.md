# Beta environment (Vercel + Supabase)

A throw-away place to try the system online. Not the shop's real data: the real
shop runs on its own server (see ONBOARDING.md). Everything here can be deleted.

| Piece | Where |
|---|---|
| Database | Supabase project **vriksha-beta** (`gzycuxlmjuipfylqmqiz`, Mumbai). Schema is at migration `e1f2a3b4c5d6`; **row-level security is on for every table**, so the public Supabase API key can read nothing. Only this backend (direct Postgres login) can. |
| Backend | Vercel project (FastAPI, entry `app/main.py`, config in `vercel.json`) |
| Website | Vercel project from the `VrikshaStudio` repo |
| Scan / 3D files | A **private** Supabase Storage bucket, through its S3 API |

## Backend environment variables (Vercel, Production and Preview)

| Name | Value |
|---|---|
| `DATABASE_URL` | Supabase **Transaction pooler** string (port 6543): `postgresql://postgres.gzycuxlmjuipfylqmqiz:<DB PASSWORD>@<pooler host>:6543/postgres`. Copy it from Supabase, Connect. The app uses no connection pool of its own on Vercel (`app/db/session.py`). |
| `ENV` | `beta` |
| `SECRET_KEY` | a long random string (the app refuses to start with the dev default) |
| `ALGORITHM` / `ACCESS_TOKEN_EXPIRE_MINUTES` | `HS256` / `30` |
| `CORS_ORIGINS` | the beta website address, e.g. `https://vriksha-beta.vercel.app` |
| `MEDIA_DIR`, `INVOICES_DIR` | `/tmp/media`, `/tmp/invoices` (Vercel's disk is read-only except `/tmp`) |
| `S3_BUCKET` | the private bucket name, e.g. `vriksha-private` |
| `S3_ENDPOINT_URL` | `https://gzycuxlmjuipfylqmqiz.supabase.co/storage/v1/s3` |
| `S3_REGION` | `ap-south-1` |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | Supabase, Storage, S3 Connection, **New access key** |

## One-time steps

1. **Database password.** Supabase, Project Settings, Database: set or reset the password and keep it safe. It is never put in git.
2. **First owner login.** On your PC, with the **session pooler or direct** address (port 5432) as `DATABASE_URL`:
   ```bash
   python -m app.cli create-owner
   ```
3. **Bucket.** Supabase, Storage: create a **private** bucket, then Settings, S3 Connection: enable it and create the access key above.
4. **Later schema changes.** Run `alembic upgrade head` from your PC with the 5432 address (not the 6543 transaction pooler).

## Known limits of the beta

- **Public photos and `.glb` uploads do not persist.** They are written to `/tmp`, which Vercel wipes. The shop's own server keeps them on disk. (To fix: store public files in a public Supabase bucket; not built.)
- Scan and result files **do** persist (they go straight to the private bucket).
- Supabase has no bucket lifecycle rules, so the 60-day deletion of source scans is not automatic here.
- Rate limiting is per serverless instance, so it is approximate.
- Cold starts add a second or two to the first request after a quiet period.
- Supabase's free plan pauses a project after a week with no activity.
