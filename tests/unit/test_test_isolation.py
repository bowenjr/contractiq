"""The suite must never open the repo-local development database."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).parents[2]


def test_default_paths_point_at_a_temporary_root() -> None:
    real = (REPO / "data").resolve()
    for name in (
        "CONTRACTIQ_DB_PATH",
        "CONTRACTIQ_DOCUMENT_ROOT",
        "CONTRACTIQ_INTAKE_STORAGE_ROOT",
        "CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT",
    ):
        path = Path(os.environ[name]).resolve()
        assert not path.is_relative_to(real), f"{name} points inside the repo data directory"


def test_bare_app_import_does_not_touch_the_repo_database() -> None:
    sys.modules.pop("app", None)
    import app

    try:
        assert Path(app.db.db_path).resolve() != (REPO / "data" / "contractiq.db").resolve()
    finally:
        sys.modules.pop("app", None)
