from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from sloppy_checker import __version__
from sloppy_checker.api.routes import router
from sloppy_checker.core.config import get_settings
from sloppy_checker.core.database import AnalysisRow, SessionLocal, create_schema
from sloppy_checker.core.methodology import load_methodology
from sloppy_checker.core.security import add_security_headers

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_adapters()
    if settings.database_url.startswith("sqlite"):
        create_schema()
    load_methodology()
    if settings.document_store == "filesystem":
        settings.document_store_path.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        active = db.query(AnalysisRow).filter(AnalysisRow.state.in_(["queued", "running"])).all()
        for row in active:
            if settings.analysis_dispatcher == "inline":
                row.state = "failed"
                row.stage = "Analysis failed"
                row.error = "Inline analysis was interrupted when the API process restarted"
        db.commit()
    yield


app = FastAPI(
    title="Sloppy Paper Checker API",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if settings.env == "development" else None,
    redoc_url=None,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
app.middleware("http")(add_security_headers)
app.include_router(router)


@app.get("/healthz", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
