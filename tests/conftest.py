import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Use a concurrent-safe workspace temp directory on restricted Windows hosts."""

    root = (Path.cwd() / ".pytest-tmp").resolve()
    root.mkdir(exist_ok=True)
    path = (root / uuid4().hex).resolve()
    path.mkdir()
    yield path
    if root not in path.parents:
        raise RuntimeError("refusing to remove a test path outside .pytest-tmp")
    shutil.rmtree(path)
