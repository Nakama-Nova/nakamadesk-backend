# Deploying on your own VM (Docker)

The API, its database, HTTPS and nightly backups run on one VM with Docker Compose. This replaces Vercel for the API. It is step 1 of [MICROSERVICES_DESIGN.md](MICROSERVICES_DESIGN.md).

```
internet -> Caddy (HTTPS, ports 80/443) -> api (uvicorn, 2 workers) -> db (Postgres 16)
                                            migrate (runs once per deploy)   backup (nightly to R2)
```
The database sits on a Docker network with no route out. Only Caddy publishes ports.

## What you need
- A VM with 8 GB RAM running Ubuntu 24.04, in Mumbai or Bangalore. Add 4 GB of swap.
- Docker Engine with the Compose plugin (`docker compose version` works).
- A domain whose A record points at the VM, for example `api.yourdomain.in`.
- Firewall: allow 22, 80 and 443 only.
- A Cloudflare R2 bucket for backups (for example `nakama-backups`) and an API token that can write to **only that bucket**.

You create the accounts, tokens and passwords yourself. Never paste them into chat or commit them.

## First deploy
```bash
git clone https://github.com/Nakama-Nova/nakamadesk-backend.git
cd nakamadesk-backend/deploy/compose
cp .env.example .env
nano .env        # SITE_ADDRESS, POSTGRES_PASSWORD, SECRET_KEY, backup keys
docker compose up -d --build
docker compose ps
curl https://api.yourdomain.in/health/
```
Make the two secrets with `openssl rand -hex 24` (database password) and `openssl rand -hex 32` (SECRET_KEY). The compose file refuses to start if either is empty.

On the first start the `migrate` container builds the whole schema from the migrations. Then create the owner account: `docker compose exec api python -m app.cli create-owner`.

## Moving from Vercel (do this in a quiet hour)
1. Deploy the stack above and check `/health/`.
2. Stop writes: tell staff to pause, or set the Vercel project to maintenance.
3. Dump the current production database, for example `pg_dump --format=custom --no-owner "<current DATABASE_URL>" -f prod.dump`.
4. Load it into the new database: `docker compose stop api`, then
   `docker compose exec -T db pg_restore --clean --if-exists --no-owner -U nakama -d nakama < prod.dump`, then `docker compose run --rm migrate` and `docker compose start api`.
5. Copy the product photos: the `media` folder from the old host into the `media` volume (`docker cp <folder>/. $(docker compose ps -q api):/data/media/`).
6. Point `API_BASE_URL` in the desktop and phone apps, and the storefront's API setting, at the new domain. Set `CORS_ORIGINS` to the storefront origin.
7. Keep Vercel up for a week as a rollback, then remove it.

## Shipping a new version
```bash
cd nakamadesk-backend && git pull
cd deploy/compose
API_TAG=$(git rev-parse --short HEAD) docker compose up -d --build
```
Order of events: the image builds, `migrate` runs, then the API restarts. Migrations must be additive (see ONBOARDING) because the old API serves until the new one is up. **Roll back** by checking out the previous commit and running the same command. A migration that dropped something cannot be rolled back this way; restore from a backup instead.

## Backups
- Every 24 hours (and once at start-up) the `backup` container writes a `pg_dump` to `<bucket>/db/` and copies the media folder to `<bucket>/media/`. Dumps older than `BACKUP_KEEP_DAYS` (30) are deleted.
- **Worst case you lose up to a day of data.** Continuous archiving is the next improvement if that is too much.
- Set `BACKUP_PING_URL` to a free healthchecks.io check so you get an email when a night is missed.
- **Test the restore once a month**, and after any change to the backup setup:
  ```bash
  docker compose run --rm --no-deps backup /scripts/restore-test.sh
  ```
  It restores the newest dump into a throwaway database, compares row counts with the live one, and drops it. A backup you have not restored is a hope, not a backup.
- To restore for real after a disaster: start the stack, stop `api`, `pg_restore --clean --if-exists` the newest dump into `nakama`, run `migrate`, start `api`.

## Day-to-day
```bash
docker compose logs -f api          # live logs
docker compose ps                   # health of each container
docker compose exec db psql -U nakama nakama
df -h; docker system df             # disk
```
Apply OS security updates (`unattended-upgrades`) and watch disk space: the database and media volumes live on the VM disk.

## Settings
All in `deploy/compose/.env`; see `.env.example`. The API also reads the variables listed in `app/core/config.py` (for example `GPS_MAX_ACCURACY_M`); to set one, add it under `api.environment` in the compose file.

## What CI checks
The `docker` job in `.github/workflows/backend-ci.yml` builds these images, starts the stack, checks migrations ran and the API answers as a non-root user, then takes a backup and restores it into a scratch database. It does **not** test real HTTPS, a real R2 bucket or your domain; check those yourself on first deploy.
