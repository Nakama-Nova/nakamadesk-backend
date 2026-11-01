# Local development with Docker

Runs the API and a Postgres database on your own computer, with the same images that will later run on a server. Production is described in [DEPLOY.md](DEPLOY.md).

## What you need
- A computer where **Docker Desktop works**. That needs hardware virtualization switched on in the BIOS (Windows: Task Manager, Performance, CPU, "Virtualization: Enabled"). If it says Disabled and you have no BIOS password, use another computer.
- Git.

## Start it
```bash
git clone https://github.com/Nakama-Nova/nakamadesk-backend.git
cd nakamadesk-backend
git checkout feature/local-dev-stack      # until it is merged into main
cd deploy/compose
docker compose --env-file dev.env -f docker-compose.yml -f docker-compose.dev.yml up --build
```
The first run downloads and builds for a few minutes. When the log says `Application startup complete`:

| What | Where |
|---|---|
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Health check | http://localhost:8000/health/ |
| Database | `localhost:5433`, user `nakama`, password `devonlypassword`, database `nakama` (pgAdmin works) |

Stop with Ctrl+C. `docker compose ... down` removes the containers and keeps your data; add `-v` to wipe the database too.

## First login
In a second terminal, in the same folder:
```bash
docker compose --env-file dev.env -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.cli create-owner
```
It asks for a username, email and password.

## Editing code
`app/` on your computer is mounted into the container and the server reloads when you save. Changes to `requirements.txt` or `alembic/` need `up --build` again. A new migration runs on the next `up`.

## Running the tests
The tests need Postgres but not the container. Create a **separate** database first (the tests wipe whatever database they are pointed at):
```bash
docker compose --env-file dev.env -f docker-compose.yml -f docker-compose.dev.yml exec db psql -U nakama -c "create database nakama_test"
cd ../..
python -m venv .venv && source .venv/bin/activate        # Windows Git Bash: .venv/Scripts/activate
pip install -r requirements.txt -r requirements-dev.txt
ENV=test DATABASE_URL=postgresql://nakama:devonlypassword@localhost:5433/nakama_test PGTZ=UTC SECRET_KEY=test_secret_key_ci pytest --ignore=tests/performance
```
`PGTZ=UTC` matches CI; without it a few time-based tests fail on a computer set to a local timezone.

## Trying the production setup locally
Adding `--profile prod-like` before `up` (a flag of `docker compose` itself) also starts Caddy and the backup container. Backups need R2 settings in the environment, so skip this unless you are testing them.

## Trouble
- **Port 8000 or 5433 in use:** stop what is using it, or change the left-hand number under `ports`.
- **`SECRET_KEY` or `POSTGRES_PASSWORD` is empty:** you left out `--env-file dev.env`.
- **Database errors after an upgrade:** `docker compose ... down -v` and start again (this deletes the local data).
