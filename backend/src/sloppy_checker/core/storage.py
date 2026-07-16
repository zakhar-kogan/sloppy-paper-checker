from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .config import AppSettings
from .schemas import PaperDocument


class DocumentStore(Protocol):
    def put(self, document: PaperDocument) -> str: ...

    def get(self, object_key: str) -> PaperDocument: ...

    def delete(self, object_key: str) -> None: ...


class FilesystemDocumentStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def put(self, document: PaperDocument) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        key = f"{uuid4()}.json"
        (self.root / key).write_text(document.model_dump_json(), encoding="utf-8")
        return key

    def get(self, object_key: str) -> PaperDocument:
        return PaperDocument.model_validate_json(self._path(object_key).read_text(encoding="utf-8"))

    def delete(self, object_key: str) -> None:
        self._path(object_key).unlink(missing_ok=True)

    def _path(self, object_key: str) -> Path:
        path = (self.root / object_key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid document object key")
        return path


class S3DocumentStore:
    def __init__(self, settings: AppSettings, client: object | None = None):
        if not settings.s3_bucket:
            raise ValueError("SPC_S3_BUCKET is required for the S3 document store")
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                region_name=settings.s3_region,
                aws_access_key_id=settings.s3_access_key_id,
                aws_secret_access_key=settings.s3_secret_access_key,
            )
        self.client = client
        self.bucket = settings.s3_bucket

    def put(self, document: PaperDocument) -> str:
        key = f"documents/{uuid4()}.json"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=document.model_dump_json().encode("utf-8"),
            ContentType="application/json",
        )
        return key

    def get(self, object_key: str) -> PaperDocument:
        response = self.client.get_object(Bucket=self.bucket, Key=object_key)
        return PaperDocument.model_validate_json(response["Body"].read())

    def delete(self, object_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=object_key)


def get_document_store(settings: AppSettings, *, s3_client: object | None = None) -> DocumentStore:
    if settings.document_store == "filesystem":
        return FilesystemDocumentStore(settings.document_store_path)
    if settings.document_store == "s3":
        return S3DocumentStore(settings, s3_client)
    raise ValueError(f"Unsupported document store: {settings.document_store}")
