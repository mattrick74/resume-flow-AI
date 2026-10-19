from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from resumeflow.db.models import Base
from resumeflow.db.types import UTCDateTime

config = context.config
target_metadata = Base.metadata


def _render_item(type_: str, obj, autogen_context):
    # Keep migrations frozen: emit the storage type, not the app-level decorator.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    return False


def _configure_and_run(connection: Connection) -> None:
    # render_as_batch: SQLite needs table rebuilds for most ALTER operations.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # The sidecar passes its own connection so SQLite pragmas are applied.
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure_and_run(connection)
        return

    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        _configure_and_run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
