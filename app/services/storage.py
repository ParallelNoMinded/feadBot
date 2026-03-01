from io import BytesIO

import aioboto3
import structlog

from app.config.settings import get_settings

logger = structlog.get_logger(__name__)


class S3Storage:
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket = settings.S3_BUCKET
        self.session = aioboto3.Session()
        self.s3_config = {
            "endpoint_url": settings.S3_ENDPOINT,
            "aws_access_key_id": settings.S3_ACCESS_KEY_ID,
            "aws_secret_access_key": settings.S3_SECRET_ACCESS_KEY,
        }

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Upload bytes to S3 asynchronously."""
        async with self.session.client("s3", **self.s3_config) as s3:
            await s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )

    async def delete_object(self, s3_key: str) -> None:
        """Delete object from S3 asynchronously.

        Args:
            s3_key: S3 object key to delete

        Raises:
            ValueError: if s3_key is None or empty
            Exception: if deletion fails
        """
        if not s3_key:
            raise ValueError("s3_key cannot be None or empty")

        try:
            async with self.session.client("s3", **self.s3_config) as s3:
                await s3.delete_object(Bucket=self.bucket, Key=s3_key)
        except Exception as exc:
            logger.error(
                "Failed to delete object from S3",
                s3_key=s3_key,
                bucket=self.bucket,
                error=str(exc),
                exc_info=True,
            )
            raise

    async def download_bytes(self, s3_key: str) -> bytes:
        """Download object plain key and return bytes asynchronously."""
        async with self.session.client("s3", **self.s3_config) as s3:
            buf = BytesIO()
            await s3.download_fileobj(self.bucket, s3_key, buf)
            buf.seek(0)
            return buf.read()
