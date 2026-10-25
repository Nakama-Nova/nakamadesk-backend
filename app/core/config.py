import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "NakamaDesk")

    ENV: str = os.getenv("ENV", "dev")

    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    DEV_DATABASE_URL: str = os.getenv("DEV_DATABASE_URL", "")
    TEST_DATABASE_URL: str = os.getenv("TEST_DATABASE_URL", "")

    # --- Public website API -------------------------------------------------
    # Where product photos / .glb models are stored on disk (served at /media).
    MEDIA_DIR: str = os.getenv("MEDIA_DIR", "media")
    # Pincodes we deliver to, comma separated. Empty = no restriction yet.
    DELIVERY_PINCODES: list = [
        v.strip() for v in os.getenv("DELIVERY_PINCODES", "").split(",") if v.strip()
    ]
    # Storefront origins allowed to call the API from a browser (CORS).
    CORS_ORIGINS: list = [
        v.strip() for v in os.getenv("CORS_ORIGINS", "").split(",") if v.strip()
    ]
    # Max checkout/enquiry/track calls per IP per minute (in-process limiter).
    PUBLIC_RATE_LIMIT: int = int(os.getenv("PUBLIC_RATE_LIMIT", "20"))

    if not DATABASE_URL:
        if ENV == "test":
            DATABASE_URL = os.getenv(
                "TEST_DATABASE_URL",
                "postgresql://postgres:postgres@localhost:5432/furnbiz_test",
            )
        else:
            DATABASE_URL = os.getenv(
                "DEV_DATABASE_URL",
                "postgresql://postgres:postgres@localhost:5432/furnbiz_dev",
            )


settings = Settings()
