import asyncio
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.datastructures import Headers

from core.schemas import Bid


class JsonRequest:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def json(self) -> dict[str, object]:
        return dict(self.payload)


def _json(response: JSONResponse) -> dict[str, object]:
    return cast(dict[str, object], json.loads(bytes(response.body)))


def _html(response: HTMLResponse) -> str:
    return bytes(response.body).decode()


@pytest.fixture
def requirement_ui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "managed"))
    sys.modules.pop("app", None)
    app_path = Path(__file__).parents[2] / "app.py"
    spec = importlib.util.spec_from_file_location("app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load app.py for requirement UI tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    return module


def _upload() -> UploadFile:
    return UploadFile(
        file=io.BytesIO(b"synthetic controlled bytes"),
        filename="Synthetic RFP.txt",
        headers=Headers({"content-type": "text/plain"}),
    )


def _register_source(module: ModuleType, bid: Bid) -> dict[str, object]:
    response = asyncio.run(
        module.register_controlled_document(
            file=_upload(),
            bid_id=bid.bid_id,
            title="Synthetic RFP",
            category="SOLICITATION",
            version_label="Original",
            document_number="RFP-1",
            issuer="Synthetic issuer",
            notes=None,
            issued_date=None,
            received_at=None,
            actor="author",
        )
    )
    return _json(response)


def _create(module: ModuleType, payload: dict[str, object]) -> dict[str, object]:
    response = asyncio.run(
        module.create_requirement(cast(Request, JsonRequest({**payload, "actor": "author"})))
    )
    assert response.status_code == 201
    return _json(response)


def test_empty_register_dashboard_and_navigation_render_without_alice(
    requirement_ui_app: ModuleType,
) -> None:
    request = cast(Request, object())
    with patch.object(
        requirement_ui_app.llm_client,
        "health_check",
        side_effect=AssertionError("Requirements attempted an Alice/network call"),
    ) as health:
        page = asyncio.run(requirement_ui_app.requirements_register(request))
        dashboard = asyncio.run(requirement_ui_app.index(request))
        my_day = asyncio.run(requirement_ui_app.my_day(request, "2026-08-05"))
    assert page.status_code == dashboard.status_code == my_day.status_code == 200
    assert "No requirements match" in _html(page)
    assert "no data" in _html(page)
    assert 'href="/requirements"' in _html(dashboard)
    assert "Requirement attention" in _html(my_day)
    health.assert_not_called()


def test_ui_creates_explicit_and_internal_and_hides_storage_identity(
    requirement_ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    requirement_ui_app.bid_repository.create_bid(valid_bid)
    controlled = _register_source(requirement_ui_app, valid_bid)
    version = cast(dict[str, object], controlled["version"])
    explicit = _create(
        requirement_ui_app,
        {
            "bid_id": valid_bid.bid_id,
            "title": "Mandatory submission",
            "statement": "Submit the synthetic schedule.",
            "origin": "EXPLICIT",
            "category": "SUBMISSION",
            "significance": "MANDATORY",
            "source_document_version_id": version["document_version_id"],
            "source_clause": "4.2",
            "due_date": "2026-08-04",
        },
    )
    internal = _create(
        requirement_ui_app,
        {
            "bid_id": valid_bid.bid_id,
            "title": "Internal review",
            "statement": "Complete internal review.",
            "origin": "INTERNAL",
            "category": "COMMERCIAL",
            "significance": "SCORED",
        },
    )
    register = asyncio.run(
        requirement_ui_app.requirements_register(
            cast(Request, object()), bid_id=valid_bid.bid_id, as_of="2026-08-05"
        )
    )
    page = _html(register)
    assert "Mandatory submission" in page and "Internal review" in page
    assert "TASK-06 readiness" in page and "does not clear" in page
    assert str(requirement_ui_app.MANAGED_DOCUMENT_ROOT) not in page
    assert "versions/" not in page
    detail = asyncio.run(
        requirement_ui_app.requirement_detail(
            cast(Request, object()), str(explicit["requirement_id"])
        )
    )
    detail_text = _html(detail)
    assert str(version["document_version_id"]) in detail_text
    assert "Synthetic RFP" in detail_text
    assert "Immutable primary source" in detail_text
    assert "requirement created" in detail_text.lower()
    assert str(requirement_ui_app.MANAGED_DOCUMENT_ROOT) not in detail_text
    assert internal["source_document_version_id"] is None
    bid_page = asyncio.run(requirement_ui_app.bid_detail(cast(Request, object()), valid_bid.bid_id))
    assert "canonical bid context" in _html(bid_page)
    assert "Mandatory submission" in _html(bid_page)
    assert "TASK-06 readiness remains authoritative" in _html(bid_page)


def test_ui_workflow_review_withdrawal_and_stale_error(
    requirement_ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    requirement_ui_app.bid_repository.create_bid(valid_bid)
    item = _create(
        requirement_ui_app,
        {
            "bid_id": valid_bid.bid_id,
            "title": "Internal response",
            "statement": "Complete a synthetic response.",
            "origin": "INTERNAL",
            "category": "OTHER",
            "significance": "MANDATORY",
        },
    )
    requirement_id = str(item["requirement_id"])
    workflow = asyncio.run(
        requirement_ui_app.update_requirement_workflow(
            requirement_id,
            cast(
                Request,
                JsonRequest(
                    {
                        "expected_version": item["version"],
                        "disposition": "COMPLY",
                        "response_text": "Synthetic response recorded.",
                        "work_state": "COMPLETE",
                        "actor": "author",
                    }
                ),
            ),
        )
    )
    worked = _json(workflow)
    reviewed = _json(
        asyncio.run(
            requirement_ui_app.review_requirement(
                requirement_id,
                cast(
                    Request,
                    JsonRequest(
                        {
                            "expected_version": worked["version"],
                            "review_state": "ACCEPTED",
                            "reviewer": "independent reviewer",
                            "actor": "reviewer",
                        }
                    ),
                ),
            )
        )
    )
    assert reviewed["review_state"] == "ACCEPTED"
    with pytest.raises(HTTPException) as stale:
        asyncio.run(
            requirement_ui_app.update_requirement_metadata(
                requirement_id,
                cast(
                    Request,
                    JsonRequest({"expected_version": 1, "owner": "late owner", "actor": "author"}),
                ),
            )
        )
    assert stale.value.status_code == 409
    withdrawn = _json(
        asyncio.run(
            requirement_ui_app.withdraw_requirement(
                requirement_id,
                cast(
                    Request,
                    JsonRequest({"expected_version": reviewed["version"], "actor": "author"}),
                ),
            )
        )
    )
    assert withdrawn["lifecycle_state"] == "WITHDRAWN"
    detail = asyncio.run(
        requirement_ui_app.requirement_detail(cast(Request, object()), requirement_id)
    )
    assert "read-only" in _html(detail)
    assert "Requirement Withdrawn" in _html(detail)


def test_ui_source_choices_are_bid_scoped_and_validation_is_visible(
    requirement_ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    other = valid_bid.model_copy(update={"bid_id": "B-2026-0002", "project_name": "Other"})
    requirement_ui_app.bid_repository.create_bid(valid_bid)
    requirement_ui_app.bid_repository.create_bid(other)
    controlled = _register_source(requirement_ui_app, other)
    version = cast(dict[str, object], controlled["version"])
    choices = _json(asyncio.run(requirement_ui_app.requirement_source_choices(valid_bid.bid_id)))
    assert choices["available"] == []
    before = requirement_ui_app.bid_repository.list_audit(valid_bid.bid_id)
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(
            requirement_ui_app.create_requirement(
                cast(
                    Request,
                    JsonRequest(
                        {
                            "bid_id": valid_bid.bid_id,
                            "title": "Cross bid",
                            "statement": "Synthetic statement",
                            "origin": "EXPLICIT",
                            "category": "OTHER",
                            "significance": "MANDATORY",
                            "source_document_version_id": version["document_version_id"],
                            "source_clause": "1",
                            "actor": "author",
                        }
                    ),
                )
            )
        )
    assert rejected.value.status_code == 422
    assert requirement_ui_app.bid_repository.list_audit(valid_bid.bid_id) == before
    page = asyncio.run(requirement_ui_app.requirements_register(cast(Request, object())))
    assert "data.detail" in _html(page)
    assert 'aria-live="assertive"' in _html(page)
