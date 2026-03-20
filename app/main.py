from fastapi import FastAPI

from app.api import auth, customers, dashboard, health, invoices, items, reports, sales, raw_materials, bom, attendance, wages
from app.db.base import Base
from app.db.session import engine
from app.models.customer import Customer
from app.models.item import Item
from app.models.sale import Sale
from app.models.sale_item import SaleItem
from app.models.user import User


app = FastAPI(title="NakamaDesk API", version="0.1.0")

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


@app.get("/")
def root():
    return {"message": "NakamaDesk Backend Running"}
