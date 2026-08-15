import pytest
import pytest_asyncio

from app.infrastructure.db.session import db_session_context
from app.infrastructure.storage.minio_storage import MinioStorage


@pytest_asyncio.fixture
async def db_session():
    async with db_session_context() as session:
        yield session


@pytest.fixture
def minio_storage() -> MinioStorage:
    return MinioStorage()