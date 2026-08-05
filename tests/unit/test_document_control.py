from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from core.document_control import (
    ControlledDocument,
    DocumentCategory,
    DocumentCreate,
    DocumentLifecycle,
    DocumentMetadataEdit,
    DocumentVersion,
    DocumentVersionCreate,
    DocumentVersionState,
)
from core.schemas import Provenance

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)


def test_create_trims_text_and_rejects_whitespace() -> None:
    request = DocumentCreate(
        bid_id=" B-2026-0001 ",
        title=" Synthetic RFP ",
        category=DocumentCategory.SOLICITATION,
        version_label=" Original ",
    )
    assert (request.bid_id, request.title, request.version_label) == (
        "B-2026-0001",
        "Synthetic RFP",
        "Original",
    )
    with pytest.raises(ValidationError, match="title must be non-empty"):
        DocumentCreate(
            bid_id="B-2026-0001",
            title="  ",
            category="SOLICITATION",
            version_label="Original",
        )
    with pytest.raises(ValidationError, match="version_label must be non-empty"):
        DocumentCreate(
            bid_id="B-2026-0001",
            title="RFP",
            category="SOLICITATION",
            version_label="\t",
        )


def test_closed_categories_and_states_reject_free_form_values() -> None:
    with pytest.raises(ValidationError):
        DocumentCreate(
            bid_id="B-2026-0001",
            title="RFP",
            category="FREE_FORM",
            version_label="Original",
        )
    with pytest.raises(ValidationError):
        ControlledDocument(
            document_id=f"DOC-{UUID(int=1)}",
            bid_id="B-2026-0001",
            title="RFP",
            category="SOLICITATION",
            lifecycle_state="DELETED",
            current_version_id=f"DV-{UUID(int=2)}",
            created_at=NOW,
            updated_at=NOW,
            version=1,
            provenance=Provenance.from_human("jason"),
        )


def test_metadata_edit_requires_change_and_preserves_nullable_fields() -> None:
    with pytest.raises(ValidationError, match="at least one editable field"):
        DocumentMetadataEdit(expected_version=1)
    edit = DocumentMetadataEdit(expected_version=1, issuer="  ", notes=None)
    assert edit.issuer is None
    assert {"issuer", "notes"} <= edit.model_fields_set


def test_version_request_requires_expected_current_version() -> None:
    with pytest.raises(ValidationError, match="expected_current_version_id"):
        DocumentVersionCreate(
            version_label="Rev 1",
            expected_document_version=1,
            expected_current_version_id=" ",
        )


def test_version_evidence_rejects_invalid_hash_and_state() -> None:
    data = {
        "document_version_id": f"DV-{UUID(int=2)}",
        "document_id": f"DOC-{UUID(int=1)}",
        "version_label": "Original",
        "original_filename": "rfp.bin",
        "byte_size": 4,
        "sha256_digest": "A" * 64,
        "storage_key": "versions/00/file.bin",
        "version_state": DocumentVersionState.CURRENT,
        "created_at": NOW,
        "provenance": Provenance.from_human("jason"),
    }
    with pytest.raises(ValidationError):
        DocumentVersion(**data)
    data["sha256_digest"] = "0" * 64
    data["version_state"] = "EDITABLE"
    with pytest.raises(ValidationError):
        DocumentVersion(**data)


def test_lifecycle_vocabulary_is_exact() -> None:
    assert {state.value for state in DocumentLifecycle} == {"ACTIVE", "WITHDRAWN"}
    assert {state.value for state in DocumentVersionState} == {"CURRENT", "SUPERSEDED"}
