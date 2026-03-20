# NakamaDesk Backend 🚀

The high-performance FastAPI backend for the **FurniBiz ERP** system. Designed to handle inventory management, retail billing, customer relationships, and business analytics.

---

## ✨ Features

- **Inventory Management**: Real-time stock tracking and low-stock alerts.
- **Sales POS**: Robust billing engine with GST calculations and invoice generation.
- **Customer CRM**: Phone-based lookup and transaction history.
- **Dashboard Analytics**: Instant business metrics (Revenue, Sales Count, Items Sold).
- **Invoicing**: Automatic PDF generation and storage management.
- **Secure Auth**: JWT-based authentication for desktop clients.

## 🛠 Tech Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com/)
- **Database**: [PostgreSQL](https://www.postgresql.org/)
- **ORM**: [SQLAlchemy 2.0](https://www.sqlalchemy.org/)
- **Migrations**: [Alembic](https://alembic.sqlalchemy.org/)
- **Validation**: [Pydantic v2](https://docs.pydantic.dev/)
- **PDF Core**: [ReportLab](https://www.reportlab.com/)

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- PostgreSQL instance running

### Installation

1. **Clone the repository**:
   ```bash
   git clone <repo-url>
   cd nakama-backend
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: .\venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Setup Environment**:
   Create a `.env` file in the root directory:
   ```env
   DATABASE_URL=postgresql://user:password@localhost/furnibiz
   SECRET_KEY=your_super_secret_key
   ALGORITHM=HS256
   ACCESS_TOKEN_EXPIRE_MINUTES=30
   ```

5. **Run Migrations**:
   ```bash
   alembic upgrade head
   ```

6. **Start the Server**:
   ```bash
   uvicorn app.main:app --reload
   ```

---

## 🧪 Testing

Run the automated test suite using `pytest`:

```bash
pytest
```

---

## 📁 Project Structure

```text
nakama-backend/
├── alembic/            # Database migrations
├── app/
│   ├── api/            # Route handlers (REST endpoints)
│   ├── core/           # Configuration and security settings
│   ├── db/             # Database session and dependency
│   ├── models/         # SQLAlchemy database models
│   ├── schemas/        # Pydantic data validation models
│   ├── services/       # Business logic (Sales, Invoices, etc.)
│   └── main.py         # App entry point
├── invoices/           # (Generated) PDF storage
├── tests/              # Pytest suite
└── .gitignore
```

---

## 📜 License

Distributed under the **MIT License**. See `LICENSE` for more information.
