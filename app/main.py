"""
NakamaDesk FastAPI Application Entry Point.

Initializes the FastAPI app, registers middleware, and includes all API routers.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import (
    assets,
    attendance,
    auth,
    bom,
    customers,
    dashboard,
    health,
    invoices,
    items,
    lab,
    media,
    orders,
    production,
    public,
    purchases,
    raw_materials,
    reports,
    sales,
    sync,
    uploads,
    wages,
)
from app.core.config import settings
from app.core.middleware import TimingMiddleware

app = FastAPI(title="NakamaDesk API", version="0.1.0")

app.add_middleware(TimingMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)  # the 3D viewer scripts are large

# The website (another origin) calls the API from the browser: allow only the
# origins listed in CORS_ORIGINS. Empty = none (default).
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(items.router)
app.include_router(sales.router)
app.include_router(reports.router)
app.include_router(customers.router)
app.include_router(invoices.router)
app.include_router(dashboard.router)
app.include_router(raw_materials.router)
app.include_router(bom.router)
app.include_router(attendance.router)
app.include_router(wages.router)
app.include_router(sync.router)
app.include_router(orders.router)
app.include_router(production.router)
app.include_router(purchases.router)
app.include_router(assets.router)
app.include_router(public.router)
app.include_router(media.router)
app.include_router(uploads.router)
app.include_router(lab.router)
app.mount("/lab/static", StaticFiles(directory=lab.STATIC_DIR), name="lab-static")


@app.get("/")
def root():
    """
    Root endpoint for health check and API status.

    Returns:
        dict: A simple status message.
    """
    return {"message": "NakamaDesk Backend Running"}
