import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "NakamaDesk")

    ENV: str = os.getenv("ENV", "dev")

    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

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
