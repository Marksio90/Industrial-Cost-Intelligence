from __future__ import annotations
import asyncio
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.middleware.auth import CurrentUser, create_access_token, get_current_user

TEST_DB_URL = "postgresql+asyncpg://ici:ici@localhost:5432/ici_test"

engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.execute(__import__("sqlalchemy").text("CREATE SCHEMA IF NOT EXISTS ici"))
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def session():
    async with TestSessionFactory() as s:
        yield s
        await s.rollback()


@pytest.fixture
def test_user() -> CurrentUser:
    return CurrentUser(
        user_id=__import__("uuid").uuid4(),
        email="test@example.com",
        tenant_id="tenant-test",
        roles=frozenset(["ICI_ENGINEER"]),
    )


@pytest.fixture
def auth_token(test_user: CurrentUser) -> str:
    return create_access_token(test_user.user_id, test_user.email, test_user.tenant_id, list(test_user.roles))


@pytest_asyncio.fixture
async def client(session: AsyncSession, test_user: CurrentUser) -> AsyncClient:
    app = create_app()

    async def override_db():
        yield session

    async def override_user():
        return test_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
