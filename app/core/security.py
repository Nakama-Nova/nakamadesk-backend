import os
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.core.config import settings

_DEV_DEFAULT_SECRET_KEY = (
    "supersecretkey_dev"  # noqa: S105 - intentional dev-only default
)

SECRET_KEY = os.getenv(
    "SECRET_KEY", _DEV_DEFAULT_SECRET_KEY
)  # Use a secure env var in prod
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

if settings.ENV not in ("dev", "test") and (
    not SECRET_KEY or SECRET_KEY == _DEV_DEFAULT_SECRET_KEY
):
    raise RuntimeError(
        f"SECRET_KEY is unset (or still the dev default) while ENV={settings.ENV!r}. "
        "Set a real SECRET_KEY in the environment before starting the app outside dev/test."
    )

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("sub") is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject",
            )
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None
