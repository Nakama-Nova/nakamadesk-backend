import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "NakamaDesk")

    ENV: str = os.getenv("ENV", "dev")

    if ENV == "test":
        DATABASE_URL: str = os.getenv(
            "TEST_DATABASE_URL",
            "postgresql://postgres:password@localhost:5432/furnibiz_test",
        )
    else:
        DATABASE_URL: str = os.getenv(
            "DEV_DATABASE_URL",
            "postgresql://postgres:password@localhost:5432/furnibiz_dev",
        )


settings = Settings()
