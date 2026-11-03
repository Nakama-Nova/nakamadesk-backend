import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "NakamaDesk")

    ENV: str = os.getenv("ENV", "dev")
    # ENV defaults to "dev", so running on Vercel also counts as production.
    IS_PROD: bool = ENV not in ("dev", "test") or bool(os.getenv("VERCEL"))

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
    # Phones reporting a GPS fix vaguer than this (metres) cannot mark attendance.
    GPS_MAX_ACCURACY_M: float = float(os.getenv("GPS_MAX_ACCURACY_M", "100"))
    # --- Email (sign-in codes and, later, order updates) ----------------------
    # Any SMTP server works (Amazon SES, Brevo, Resend...). Empty SMTP_HOST in
    # production makes "send me a code" answer 503 instead of dropping the email.
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM") or "Vriksha Studios <no-reply@localhost>"
    # Where a shopper's reply goes, if different from the sender (e.g. info@).
    EMAIL_REPLY_TO: str = os.getenv("EMAIL_REPLY_TO", "")
    # EMAIL_PROVIDER=ses sends through Amazon SES with the server's AWS role: no password to store,
    # but the sender's domain must be verified in SES. Empty = SMTP (above) when SMTP_HOST is set.
    EMAIL_PROVIDER: str = os.getenv("EMAIL_PROVIDER", "")
    SES_REGION: str = os.getenv("SES_REGION", "ap-southeast-2")
    # --- SMS (one-time codes to a shopper's phone) ------------------------------
    # SMS_PROVIDER=sns sends through Amazon SNS with the server's AWS role. Empty: in
    # development the message is only logged; in production the routes answer 503.
    SMS_PROVIDER: str = os.getenv("SMS_PROVIDER", "")
    SMS_AWS_REGION: str = os.getenv("SMS_AWS_REGION", "ap-southeast-2")
    # India (TRAI DLT): a registered header and the id of an approved template. The
    # text sent must match that template, so SMS_OTP_TEMPLATE is set to the same words.
    SMS_SENDER_ID: str = os.getenv("SMS_SENDER_ID", "")
    SMS_DLT_ENTITY_ID: str = os.getenv("SMS_DLT_ENTITY_ID", "")
    SMS_DLT_TEMPLATE_ID: str = os.getenv("SMS_DLT_TEMPLATE_ID", "")
    SMS_OTP_TEMPLATE: str = os.getenv("SMS_OTP_TEMPLATE") or (
        "Your Vriksha Studios verification code is {code}. "
        "It is valid for 10 minutes. Do not share it with anyone."
    )
    # Only numbers starting with one of these get a code. Stops anyone using the form to
    # make the shop pay for texts to expensive foreign numbers.
    SMS_ALLOWED_PREFIXES: list = [
        v.strip()
        for v in os.getenv("SMS_ALLOWED_PREFIXES", "+91").split(",")
        if v.strip()
    ]
    # Hard daily ceilings on codes sent to anyone (every account and number together). The
    # per-IP limit is per server instance and the per-account limits are beaten by making
    # new accounts, so these are what actually stop someone running up the SMS/email bill.
    SMS_DAILY_LIMIT: int = int(os.getenv("SMS_DAILY_LIMIT", "300"))
    CODE_EMAILS_DAILY_LIMIT: int = int(os.getenv("CODE_EMAILS_DAILY_LIMIT", "1000"))
    # --- Paying by cash or UPI (no payment gateway) --------------------------
    # The shop's UPI id and the name it shows under; shoppers pay after staff confirm
    # the order by phone. Empty PAY_UPI_ID = no UPI link is shown.
    PAY_UPI_ID: str = os.getenv("PAY_UPI_ID", "")
    PAY_UPI_NAME: str = os.getenv("PAY_UPI_NAME") or "Vriksha Studios"
    PAY_PHONE: str = os.getenv("PAY_PHONE", "")  # the number shoppers are told to call
    # Max checkout/enquiry/track calls per IP per minute (in-process limiter).
    PUBLIC_RATE_LIMIT: int = int(os.getenv("PUBLIC_RATE_LIMIT", "20"))

    # --- Private object store (scans, STL/OBJ/G-code, part models) -----------
    # Any S3-compatible service: AWS S3, or Cloudflare R2 via S3_ENDPOINT_URL.
    # Empty S3_BUCKET = not configured (the presign endpoints answer 503).
    S3_BUCKET: str = os.getenv("S3_BUCKET", "")
    S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "")
    S3_REGION: str = os.getenv("S3_REGION", "auto")
    S3_ACCESS_KEY_ID: str = os.getenv("S3_ACCESS_KEY_ID", "")
    S3_SECRET_ACCESS_KEY: str = os.getenv("S3_SECRET_ACCESS_KEY", "")

    # --- Desktop app updates ------------------------------------------------
    # The installers are GitHub Releases. While the repos are public no token is needed
    # (GitHub is asked anonymously, 60 questions an hour). A fine-grained token with
    # read-only "Contents" access is only for a private repo or a higher limit.
    GITHUB_RELEASES_REPO: str = os.getenv(
        "GITHUB_RELEASES_REPO", "Nakama-Nova/nakama-desktop"
    )
    GITHUB_RELEASES_TOKEN: str = os.getenv("GITHUB_RELEASES_TOKEN", "")
    # the phone app's releases (an .apk and its .sha256 attached to a v1.2.3 release)
    GITHUB_MOBILE_RELEASES_REPO: str = os.getenv(
        "GITHUB_MOBILE_RELEASES_REPO", "Nakama-Nova/vriksha-mobile"
    )

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
