import shutil
from pathlib import Path

import pytest

from agent import db
from agent.config import ROOT, Settings

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(workspace_dir=str(tmp_path / "ws"), api_key="test", model="fake/model")


@pytest.fixture
def conn(settings):
    c = db.connect(settings.workspace / "invoices.db")
    db.seed(c, ROOT / "data")
    return c


@pytest.fixture
def pdf():
    def _get(name: str) -> Path:
        return FIXTURES / "pdf" / name

    return _get


@pytest.fixture
def copy_to(tmp_path):
    def _copy(src: Path, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        return Path(shutil.copy(src, dest_dir / src.name))

    return _copy
