import hashlib
import os
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sloppy_checker.core.config import AppSettings
from sloppy_checker.core.database import AnalysisRow, Base
from sloppy_checker.core.dispatch import InlineAnalysisDispatcher, NebiusJobDispatcher
from sloppy_checker.core.repository import SqlAlchemyAnalysisRepository
from sloppy_checker.core.schemas import ContentLevel, PaperDocument, SourceFormat
from sloppy_checker.core.storage import FilesystemDocumentStore, S3DocumentStore


def paper() -> PaperDocument:
    text = "Canonical document"
    return PaperDocument(
        content_level=ContentLevel.FULL_TEXT,
        source_format=SourceFormat.PDF,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        parser_name="test",
        parser_version="1",
        text=text,
    )


def test_filesystem_document_store_contract(tmp_path):
    store = FilesystemDocumentStore(tmp_path)
    key = store.put(paper())
    assert store.get(key) == paper()
    store.delete(key)
    assert not (tmp_path / key).exists()


def test_sqlite_repository_contract(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'repository.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = SqlAlchemyAnalysisRepository(session)
        row = repository.add(
            AnalysisRow(
                source={"kind": "document", "value": "document-id"},
                request={"_owner_hash": "owner", "provider_runtime": {"mode": "hosted"}},
                events=[],
            )
        )
        assert repository.get(row.id) is row
        assert repository.count_active("owner") == 1
        assert repository.count_recent("owner", "hosted") == 1
        row.state = "completed"
        repository.save(row)
        assert repository.count_active("owner") == 0


@pytest.mark.skipif(not os.getenv("SPC_TEST_POSTGRES_URL"), reason="PostgreSQL test URL not configured")
def test_postgresql_repository_contract():
    engine = create_engine(os.environ["SPC_TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    owner = "contract-owner-postgresql"
    with engine.connect() as connection, connection.begin() as transaction:
        with Session(bind=connection) as session:
            repository = SqlAlchemyAnalysisRepository(session)
            row = repository.add(
                AnalysisRow(
                    source={"kind": "document", "value": "document-id"},
                    request={"_owner_hash": owner, "provider_runtime": {"mode": "hosted"}},
                    events=[],
                )
            )
            assert repository.get(row.id) is row
            assert repository.count_active(owner) == 1
        transaction.rollback()


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket, Key):
        return {"Body": BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


def test_s3_document_store_contract():
    fake = FakeS3()
    settings = AppSettings(document_store="s3", s3_endpoint_url="https://s3.example", s3_bucket="papers")
    store = S3DocumentStore(settings, fake)
    key = store.put(paper())
    assert store.get(key) == paper()
    store.delete(key)
    assert not fake.objects


@pytest.mark.asyncio
async def test_inline_dispatcher_uses_same_analysis_id():
    seen = []

    async def runner(analysis_id, settings):
        seen.append((analysis_id, settings.analysis_dispatcher))

    tasks = BackgroundTasks()
    dispatcher = InlineAnalysisDispatcher(AppSettings(), runner)
    assert await dispatcher.dispatch("00000000-0000-0000-0000-000000000001", tasks) is None
    await tasks()
    assert seen == [("00000000-0000-0000-0000-000000000001", "inline")]


def test_nebius_job_contains_id_and_secret_references_only():
    settings = AppSettings(
        database_url="postgresql+psycopg://placeholder",
        document_store="s3",
        analysis_dispatcher="nebius_job",
        s3_endpoint_url="https://storage.eu-north1.nebius.cloud",
        s3_bucket="papers",
        nebius_project_id="project-test",
        nebius_job_image="registry.example/spc:test",
        nebius_job_secret_id="mbsec-test",  # noqa: S106 -- opaque fixture resource ID
    )
    request = NebiusJobDispatcher(settings, SimpleNamespace()).build_request(
        "00000000-0000-0000-0000-000000000001"
    )
    environment = {item.name: item for item in request.spec.environment_variables}
    assert environment["SPC_ANALYSIS_ID"].value == "00000000-0000-0000-0000-000000000001"
    assert environment["SPC_DATABASE_URL"].mysterybox_secret.secret_id == "mbsec-test"
    serialized = request.__pb2_message__.SerializeToString()
    assert b"postgresql+psycopg" not in serialized
    assert b"paper text" not in serialized
