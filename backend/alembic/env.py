from __future__ import annotations
import asyncio
from logging.config import fileConfig
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all ORM models so metadata is populated
from src.database import Base  # noqa: E402
from src.modules.materials.infrastructure.orm import MaterialORM  # noqa: F401
from src.modules.suppliers.infrastructure.orm import SupplierORM  # noqa: F401
from src.modules.costs.infrastructure.orm import CostBreakdownORM, CostComponentORM  # noqa: F401
from src.modules.processes.infrastructure.orm import ManufacturingProcessORM  # noqa: F401
from src.modules.rfq.infrastructure.orm import RFQORM, RFQLineItemORM, RFQSupplierORM  # noqa: F401
from src.modules.quotes.infrastructure.orm import QuoteORM, QuoteLineItemORM  # noqa: F401
from src.modules.forecasting.infrastructure.orm import PriceForecastORM, DemandForecastORM, ForecastDataPointORM  # noqa: F401
from src.modules.risk.infrastructure.orm import RiskItemORM, MitigationActionORM  # noqa: F401

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}, include_schemas=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, include_schemas=True, version_table_schema="ici")
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = config.get_main_option("sqlalchemy.url")
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
