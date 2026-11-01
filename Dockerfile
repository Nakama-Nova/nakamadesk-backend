# API image. Build from the repository root:  docker build -t nakama-api .
# Same image runs the API and the one-shot `alembic upgrade head` job.

FROM python:3.11-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.txt

FROM python:3.11-slim
# ENV=production turns on the fail-fast checks (SECRET_KEY length, no /docs).
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENV=production \
    MEDIA_DIR=/data/media
RUN useradd --system --uid 10001 --create-home app \
    && mkdir -p /data/media && chown -R app /data
COPY --from=build /opt/venv /opt/venv
WORKDIR /srv
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health/', timeout=4)"
# Two workers fit the 8 GB VM plan; the compose file keeps this port off the internet,
# so trusting the proxy's forwarding headers is safe.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
