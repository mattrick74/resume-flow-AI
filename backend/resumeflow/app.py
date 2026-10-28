from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from resumeflow import __version__
from resumeflow.api import answers, applications, candidates, dashboard, health, jobs, license, operations, pair, processing, settings, system, templates
from resumeflow.auth import require_license, require_session_token
from resumeflow.config import AppConfig
from resumeflow.db.migrate import current_revision, upgrade_to_head
from resumeflow.db.session import create_db_engine, create_session_factory
from resumeflow.errors import install_error_handlers
from resumeflow.licensing import LicenseManager
from resumeflow.services.ai.service import AIService, ProviderFactory
from resumeflow.services.applications import register_handlers, resume_automatic_exports
from resumeflow.services.backups import auto_backup_if_due
from resumeflow.services.batch import register_batch_handlers
from resumeflow.services.experience import register_experience_handlers
from resumeflow.services.queue import ProcessingQueue
from resumeflow.services.secrets import KeyringSecretStore, MemorySecretStore, SecretStore
from resumeflow.services.settings import load_settings

logger = logging.getLogger(__name__)

# Host header allow-list blocks DNS-rebinding access to the loopback API.
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]


def create_app(
    config: AppConfig | None = None,
    *,
    secret_store: SecretStore | None = None,
    ai_provider_factory: ProviderFactory | None = None,
) -> FastAPI:
    config = config or AppConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config.ensure_directories()
        engine = create_db_engine(config.database_path)
        upgrade_to_head(engine)
        app.state.schema_revision = current_revision(engine)
        app.state.engine = engine
        session_factory = create_session_factory(engine)
        app.state.session_factory = session_factory
        app.state.ai = AIService(session_factory, app.state.secret_store, ai_provider_factory)

        def concurrency() -> int:
            with session_factory() as db:
                return load_settings(db).worker_concurrency

        queue = ProcessingQueue(session_factory, concurrency)
        register_handlers(queue, app.state.ai)
        register_batch_handlers(queue, app.state.ai)
        register_experience_handlers(queue, app.state.ai)
        app.state.queue = queue
        with session_factory() as db:
            resumed = resume_automatic_exports(db, queue)
        if resumed:
            logger.info("Resumed automatic export for %s application(s)", resumed)
        if config.auto_backup:
            with session_factory() as db:
                retention = load_settings(db).backup_retention_count
            auto_backup_if_due(engine, config, retention)
        if config.workers_enabled:
            queue.start()
        try:
            yield
        finally:
            queue.stop()
            app.state.ai.close()
            engine.dispose()

    app = FastAPI(
        title="ResumeFlow AI Sidecar",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = config
    app.state.license = LicenseManager(config.data_dir, required=config.license_required)
    app.state.secret_store = secret_store or (MemorySecretStore() if config.secret_backend == "memory" else KeyringSecretStore())

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[*config.allowed_origins, *(f"chrome-extension://{extension_id}" for extension_id in config.extension_ids)],
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    install_error_handlers(app)

    protected = [Depends(require_session_token), Depends(require_license)]
    app.include_router(health.router)
    app.include_router(pair.router)  # unauthenticated: exchanges a pairing code for the token
    app.include_router(license.router, dependencies=[Depends(require_session_token)])  # open without a license, to enter one
    for module in (settings, dashboard, candidates, jobs, applications, answers, processing, operations, system, templates):
        app.include_router(module.router, dependencies=protected)
    return app
