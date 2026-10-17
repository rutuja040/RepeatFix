from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import settings
from backend.core.errors import register_error_handlers
from backend.core.logging import setup_logging
from backend.api.routes import complaints, imports, meta

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Simple local init: create tables if they do not exist.
    # NOTE: create_all only creates missing tables; it never alters existing ones.
    # If columns are added later, follow the upgrade procedure in docs/architecture.md.
    from backend.db.session import Base, engine
    import backend.models  # noqa: F401  # register models

    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.project_name, version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

register_error_handlers(app)

app.include_router(complaints.router, prefix=settings.api_v1_prefix)
app.include_router(meta.router, prefix=settings.api_v1_prefix)
app.include_router(imports.router, prefix=settings.api_v1_prefix)


@app.get("/")
def root():
    return {"message": "RepeatFix API", "version": "0.3.0", "status": "ok"}
