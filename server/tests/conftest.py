import asyncio
import json
import sys
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.db.base import Base
from server.db.session import get_async_session
from server.main import app
from server.repositories.connections import ConnectionRepository
from server.services.crypto_service import get_app_encryption_key

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        future=True,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA foreign_keys=ON"))

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    TestSessionFactory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with TestSessionFactory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def test_client(test_engine) -> AsyncGenerator[AsyncClient, None]:
    TestSessionFactory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_async_session():
        async with TestSessionFactory() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_get_async_session

    # Seed a user and tenant so auth middleware doesn't reject requests
    from uuid import uuid4

    from server.models.tenant import Tenant
    from server.models.tenant_member import TenantMember, TenantRole
    from server.models.user import User

    test_user_id = uuid4()
    test_tenant_id = uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=test_user_id,
            email="test@test.com",
            hashed_password="fakehash",
            is_active=True,
            is_verified=True,
            is_superuser=False,
        )
        session.add(user)
        await session.flush()

        tenant = Tenant(
            id=test_tenant_id,
            name="Test Tenant",
            slug="community",
            owner_id=test_user_id,
            is_personal=True,
        )
        session.add(tenant)
        await session.flush()

        member = TenantMember(
            user_id=test_user_id,
            tenant_id=test_tenant_id,
            role=TenantRole.OWNER.value,
        )
        session.add(member)
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"x-tenant-id": str(test_tenant_id)},
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def setup_encryption_key(test_session):
    from uuid import uuid4

    from server.auth.tenant_context import set_tenant_id
    from server.models.tenant import Tenant
    from server.models.user import User

    # Create a user and tenant so the settings FK constraint is satisfied
    uid = uuid4()
    tid = uuid4()
    test_session.add(User(id=uid, email="enc@test.com", hashed_password="x", is_active=True, is_verified=True))
    await test_session.flush()
    test_session.add(Tenant(id=tid, name="EncTenant", slug="enc-tenant", owner_id=uid, is_personal=True))
    await test_session.commit()

    set_tenant_id(tid)
    await get_app_encryption_key(test_session)
    yield


@pytest.fixture
def sample_notebook_data():
    return {
        "notebook_name": "Test Data Analysis Notebook",
        "description": "A notebook for testing data analysis workflows",
    }


@pytest.fixture
def sample_pg_connection():
    return {
        "type": "pg",
        "connection_obj": {
            "host": "localhost",
            "port": 5432,
            "database": "testdb",
            "username": "testuser",
            "password": "testpass123",
        },
    }


@pytest.fixture
def sample_mongo_connection():
    return {
        "type": "mongo",
        "connection_obj": {"connection_string": "mongodb://mongouser:mongopass123@localhost:27017/testdb"},
    }


@pytest.fixture
def sample_sqlite_connection():
    return {"type": "sqlite", "connection_obj": {"database_path": "/tmp/test.db"}}


@pytest.fixture
def invalid_connection():
    return {"type": "invalid_db_type", "connection_obj": {"some_field": "some_value"}}


class TestDataFactory:
    @staticmethod
    def create_notebook(name_suffix: str = "") -> dict:
        return {
            "notebook_name": f"Test Notebook {name_suffix}",
            "description": f"Description for notebook {name_suffix}",
        }

    @staticmethod
    def create_connection(conn_type: str, **kwargs) -> dict:
        base_configs = {
            "pg": {
                "host": kwargs.get("host", "localhost"),
                "port": kwargs.get("port", 5432),
                "database": kwargs.get("database", "testdb"),
                "username": kwargs.get("username", "testuser"),
                "password": kwargs.get("password", "testpass"),
            },
            "mysql": {
                "host": kwargs.get("host", "localhost"),
                "port": kwargs.get("port", 3306),
                "database": kwargs.get("database", "testdb"),
                "username": kwargs.get("username", "mysqluser"),
                "password": kwargs.get("password", "mysqlpass"),
            },
            "mongo": {
                "connection_string": kwargs.get(
                    "connection_string",
                    f"mongodb://{kwargs.get('username', 'mongouser')}:{kwargs.get('password', 'mongopass')}@{kwargs.get('host', 'localhost')}:{kwargs.get('port', 27017)}/{kwargs.get('database', 'testdb')}",
                )
            },
            "sqlite": {"database_path": kwargs.get("database_path", "/tmp/test.db")},
            "mssql": {
                "host": kwargs.get("host", "localhost"),
                "port": kwargs.get("port", 1433),
                "database": kwargs.get("database", "testdb"),
                "username": kwargs.get("username", "sa"),
                "password": kwargs.get("password", "mssqlpass"),
            },
        }

        return {"type": conn_type, "connection_obj": base_configs.get(conn_type, {})}


@pytest.fixture
def test_data_factory():
    return TestDataFactory()


@pytest.fixture(autouse=True)
def mock_schema_fetch(monkeypatch):
    async def _fake_fetch_schema(connection, session):
        return {"tables": {}, "version": "mock"}

    async def _fake_refresh(connection_id, session):
        repo = ConnectionRepository(session)
        conn = await repo.get(connection_id)
        if not conn:
            raise ValueError(f"Connection {connection_id} not found")
        conn.schema_cache = json.dumps({"tables": {}, "version": "mock"})
        await session.commit()
        await session.refresh(conn)
        return conn, {"tables": {}, "version": "mock"}

    monkeypatch.setattr(
        "server.services.connections.ConnectionService.fetch_schema",
        staticmethod(_fake_fetch_schema),
    )
    monkeypatch.setattr(
        "server.services.connections.ConnectionService.refresh_connection_schema",
        staticmethod(_fake_refresh),
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "notebook: mark test as notebook-related")
    config.addinivalue_line("markers", "connection: mark test as connection-related")
    config.addinivalue_line("markers", "workflow: mark test as workflow integration test")
    config.addinivalue_line("markers", "error_handling: mark test as error handling test")
    config.addinivalue_line("markers", "conversation: mark test as conversation-related")
    config.addinivalue_line("markers", "streaming: mark test as streaming response test")
    config.addinivalue_line("markers", "handoff: mark test as Byaan Handoffs related")
