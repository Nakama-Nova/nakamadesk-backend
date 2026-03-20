from fastapi import FastAPI

from app.core.middleware import TimingMiddleware

from app.api import (
    auth,
    customers,
    dashboard,
    health,
    invoices,
    items,
    reports,
    sales,
    raw_materials,
    bom,
    attendance,
    wages,
    sync,
)

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
    return {"message": "NakamaDesk Backend Running"}
