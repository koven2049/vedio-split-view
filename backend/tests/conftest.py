from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from video_split.config import set_config_path
from video_split.database import Base, get_db

os.environ.setdefault("TESTING", "1")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_data_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("data")


@pytest.fixture(scope="session")
def test_config_path(tmp_path_factory, test_data_dir):
    config_dir = tmp_path_factory.mktemp("config")
    config_file = config_dir / "app.yaml"
    (test_data_dir / "tmp").mkdir(exist_ok=True)

    config_file.write_text(f"""
app:
  host: "0.0.0.0"
  port: 8080
  secret_key: "test-secret-key-for-testing-minimum-32-bytes-long"

admin:
  password: "test-admin-pass"

llm:
  base_url: "https://fake-llm.example.com/v1"
  model: "test-model"
  api_key: "test-key"
  timeout_ms: 5000
  max_tokens: 1024

transcription:
  base_url: "https://fake-whisper.example.com/v1"
  api_key: "test-whisper-key"
  model: "whisper-1"
  language: ""

network:
  proxy_enabled: false
  http_proxy: ""
  https_proxy: ""

storage:
  db_path: "{test_data_dir}/test.db"
  temp_dir: "{test_data_dir}/tmp"
  max_pending_tasks_per_user: 3

video:
  max_duration_seconds: 12600
  confirm_threshold_seconds: 3600
""")
    return str(config_file)


@pytest_asyncio.fixture
async def db_engine(test_config_path, tmp_path):
    set_config_path(test_config_path)
    from video_split import models  # noqa: F401  register tables on Base.metadata
    db_file = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine, test_config_path):
    set_config_path(test_config_path)

    from video_split.main import create_app

    app = create_app(use_lifespan=False)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    async with factory() as setup_session:
        from video_split.service.auth_service import ensure_admin_user
        await ensure_admin_user(setup_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def get_admin_token(client: AsyncClient) -> str:
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "test-admin-pass"})
    return resp.json()["access_token"]


async def admin_create_user(
    client: AsyncClient, username: str, password: str = "pass123", role: str = "viewer",
) -> str:
    """Create a viewer account via the admin API and log in. Returns access token.

    Only viewer accounts can be created — admin is the single seeded account.
    """
    admin_token = await get_admin_token(client)
    await client.post(
        "/api/admin/users",
        json={"username": username, "password": password, "role": role},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login_resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return login_resp.json()["access_token"]
