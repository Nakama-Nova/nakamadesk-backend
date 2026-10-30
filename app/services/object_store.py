"""
Private object store for large and staff-only files (scans, STL/OBJ/PLY,
G-code, part models).

Any S3-compatible service works with the same code: AWS S3, or Cloudflare R2
by setting S3_ENDPOINT_URL. The server only SIGNS urls and checks objects; the
bytes go straight between the client and the store, so a small server is
enough. Clients (phones, the GPU PC) never hold cloud keys, only short-lived
presigned URLs.

Tests replace the store with an in-memory fake (see get_store).
"""

from typing import Dict, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException

from app.core.config import settings


class S3Store:
    def __init__(self) -> None:
        self.bucket = settings.S3_BUCKET
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL or None,
            region_name=settings.S3_REGION or "auto",
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            config=Config(
                signature_version="s3v4",
                # S3-compatible services (Supabase, R2, MinIO) want /bucket/key URLs
                s3={"addressing_style": "path"} if settings.S3_ENDPOINT_URL else {},
            ),
        )

    def presign_put(
        self, key: str, content_type: str, size: int, expires: int = 600
    ) -> Dict:
        """
        Signed PUT for exactly `size` bytes of `content_type`: both headers are part
        of the signature, so the STORE rejects any other body. (Presigned PUT works on
        AWS S3, Cloudflare R2 and Supabase Storage; browser-style POST policies do not.)
        The client must send those two headers.
        """
        url = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": size,
            },
            ExpiresIn=expires,
            HttpMethod="PUT",
        )
        return {"url": url, "method": "PUT", "headers": {"Content-Type": content_type}}

    def presign_get(
        self, key: str, expires: int = 900, filename: Optional[str] = None
    ) -> str:
        params = {"Bucket": self.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self._client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires
        )

    def head(self, key: str) -> Optional[int]:
        """Size in bytes, or None if the object does not exist."""
        try:
            return self._client.head_object(Bucket=self.bucket, Key=key)[
                "ContentLength"
            ]
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in (
                "404",
                "NoSuchKey",
                "NotFound",
            ):
                return None
            raise

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        """Store a small file the server already holds (a public product photo)."""
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    def get_bytes(self, key: str, max_bytes: int) -> bytes:
        """Read an object (used to publish a small file to the public disk)."""
        try:
            body = self._client.get_object(Bucket=self.bucket, Key=key)["Body"]
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in (
                "404",
                "NoSuchKey",
                "NotFound",
            ):
                raise KeyError(key) from None
            raise
        return body.read(max_bytes + 1)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


def get_store_or_none() -> Optional[S3Store]:
    """For routes that only sometimes need the store (e.g. downloading a local file)."""
    return S3Store() if settings.S3_BUCKET else None


def get_store() -> S3Store:
    """FastAPI dependency. 503 until the bucket is configured."""
    if not settings.S3_BUCKET:
        raise HTTPException(
            status_code=503,
            detail="File storage is not set up yet (S3_BUCKET is empty)",
        )
    return S3Store()
