import pytest

from stockforge.config import Settings
from stockforge.db import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(
        STOCKFORGE_DB_PATH=str(tmp_path / "test.sqlite"),
        STOCKFORGE_DRY_RUN=True,
        STOCKFORGE_REQUIRE_APPROVAL=False,
        BANKR_BACKEND="rest",
        STOCKFORGE_DAILY_LAUNCH_BUDGET=3,
        _env_file=None,
    )


@pytest.fixture
async def store(tmp_path):
    s = Store(str(tmp_path / "state.sqlite"))
    await s.connect()
    yield s
    await s.close()
