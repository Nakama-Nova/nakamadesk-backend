"""
NakamaDesk FastAPI Application Entry Point.

Initializes the FastAPI app, registers middleware, and includes all API routers.
"""

from fastapi import FastAPI

from app.api import (
    attendance,
    auth,
    bom,
    customers,
    dashboard,
    health,
    invoices,
    items,
    raw_materials,
    reports,
    sales,
    sync,
    wages,
)
from app.core.middleware import TimingMiddleware

app = FastAPI(title="NakamaDesk API", version="0.1.0")

app.add_middleware(TimingMiddleware)

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


@app.get("/")
def root():
    """
    Root endpoint for health check and API status.

    Returns:
        dict: A simple status message.
    """
    return {"message": "NakamaDesk Backend Running"}
