import gc
import warnings
from unittest.mock import AsyncMock

import pytest


# Define Mock Protocol or just a class
class MockVectorStore:
    def __init__(self) -> None:
        self.upsert = AsyncMock()
        self.search = AsyncMock(return_value=[])
        self.delete = AsyncMock()
        self.find_similar_by_id = AsyncMock(return_value=[])
        self.count = AsyncMock(return_value=0)
        self.list_ids = AsyncMock(return_value=[])


@pytest.fixture
def mock_vector_store() -> MockVectorStore:
    return MockVectorStore()


@pytest.fixture(autouse=True)
def _drain_orphan_coroutines() -> None:  # type: ignore[return]
    """Force gc.collect() after each test to reap orphan AsyncMock coroutines.

    Without this, pytest's own gc sweep detects unawaited coroutines from
    MagicMock/AsyncMock cleanup and emits PytestUnraisableExceptionWarning.
    By collecting garbage here (inside the test's warning filter scope),
    the RuntimeWarning is suppressed by the filterwarnings config.
    """
    yield  # type: ignore[misc]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        gc.collect()
