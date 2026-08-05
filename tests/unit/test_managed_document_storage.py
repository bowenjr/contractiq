import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

import pytest

from core.document_control import DocumentVersion, DocumentVersionState, IntegrityStatus
from core.managed_document_storage import (
    EmptyManagedFileError,
    ManagedDocumentStorage,
    ManagedFileTooLargeError,
    StorageCollisionError,
    UnsafeStorageKeyError,
    normalize_display_filename,
)
from core.schemas import Provenance

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)


class TrackingStream(io.BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.requested_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.requested_sizes.append(size)
        return super().read(size)


def _version(key: str, content: bytes) -> DocumentVersion:
    return DocumentVersion(
        document_version_id=f"DV-{UUID(int=2)}",
        document_id=f"DOC-{UUID(int=1)}",
        version_label="Original",
        original_filename="RFP Δ.txt",
        media_type="text/plain",
        byte_size=len(content),
        sha256_digest=hashlib.sha256(content).hexdigest(),
        storage_key=key,
        version_state=DocumentVersionState.CURRENT,
        created_at=NOW,
        provenance=Provenance.from_human("jason"),
    )


def test_stage_streams_known_evidence_and_places_opaque_key(tmp_path: Path) -> None:
    content = b"synthetic-rfp-v1"
    storage = ManagedDocumentStorage(tmp_path / "managed", 1024)
    staged = storage.stage(io.BytesIO(content))
    key = storage.storage_key(f"DV-{UUID(int=2)}")
    placed = storage.place(staged, key)
    assert staged.byte_size == len(content)
    assert staged.sha256_digest == hashlib.sha256(content).hexdigest()
    assert placed.read_bytes() == content
    assert key == f"versions/00/DV-{UUID(int=2)}.bin"


def test_empty_and_oversized_streams_leave_no_staging(tmp_path: Path) -> None:
    storage = ManagedDocumentStorage(tmp_path / "managed", 4)
    with pytest.raises(EmptyManagedFileError):
        storage.stage(io.BytesIO(b""))
    with pytest.raises(ManagedFileTooLargeError):
        storage.stage(io.BytesIO(b"12345"))
    assert storage.staging_files() == []


def test_large_fixture_is_consumed_in_bounded_chunks(tmp_path: Path) -> None:
    content = b"x" * (2 * 1024 * 1024 + 17)
    source = TrackingStream(content)
    storage = ManagedDocumentStorage(tmp_path / "managed", len(content))
    staged = storage.stage(source)
    assert staged.byte_size == len(content)
    assert len(source.requested_sizes) >= 4
    assert set(source.requested_sizes) == {1024 * 1024}
    storage.remove_staged(staged)


def test_display_filename_cannot_determine_path() -> None:
    assert normalize_display_filename("../../etc/passwd") == "passwd"
    assert normalize_display_filename("..\\..\\evil\x00.txt") == "evil.txt"
    assert normalize_display_filename(" RFP Δ.pdf ") == "RFP Δ.pdf"


def test_resolution_rejects_root_escape_and_absolute_paths(tmp_path: Path) -> None:
    storage = ManagedDocumentStorage(tmp_path / "managed", 1024)
    for key in ("../secret", "/tmp/secret", "versions/../../secret"):
        with pytest.raises(UnsafeStorageKeyError):
            storage.resolve(key)


def test_placement_never_overwrites_existing_evidence(tmp_path: Path) -> None:
    storage = ManagedDocumentStorage(tmp_path / "managed", 1024)
    key = storage.storage_key(f"DV-{UUID(int=2)}")
    first = storage.stage(io.BytesIO(b"first"))
    storage.place(first, key)
    second = storage.stage(io.BytesIO(b"second"))
    with pytest.raises(StorageCollisionError):
        storage.place(second, key)
    storage.remove_staged(second)
    assert storage.resolve(key).read_bytes() == b"first"


def test_integrity_precedence_ok_missing_size_and_hash(tmp_path: Path) -> None:
    content = b"1234"
    storage = ManagedDocumentStorage(tmp_path / "managed", 1024)
    version = _version("versions/00/evidence.bin", content)
    path = storage.resolve(version.storage_key)
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    assert storage.verify(version).status == IntegrityStatus.OK
    path.write_bytes(b"longer")
    assert storage.verify(version).status == IntegrityStatus.SIZE_MISMATCH
    path.write_bytes(b"4321")
    assert storage.verify(version).status == IntegrityStatus.HASH_MISMATCH
    path.unlink()
    assert storage.verify(version).status == IntegrityStatus.MISSING


def test_unreadable_and_unsafe_keys_are_deterministic(tmp_path: Path) -> None:
    def failing_open(path: Path, mode: str) -> BinaryIO:
        raise PermissionError(f"denied: {path.name}")

    storage = ManagedDocumentStorage(tmp_path / "managed", 1024, opener=failing_open)
    version = _version("versions/00/evidence.bin", b"1234")
    path = storage.resolve(version.storage_key)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"1234")
    assert storage.verify(version).status == IntegrityStatus.UNREADABLE
    unsafe = version.model_copy(update={"storage_key": "../escape"})
    result = storage.verify(unsafe)
    assert result.status == IntegrityStatus.UNREADABLE
    assert "unsafe" in result.reason


def test_repeated_verification_is_read_only_and_equal(tmp_path: Path) -> None:
    content = b"evidence"
    storage = ManagedDocumentStorage(tmp_path / "managed", 1024)
    version = _version("versions/00/evidence.bin", content)
    path = storage.resolve(version.storage_key)
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    before = path.read_bytes()
    assert storage.verify(version) == storage.verify(version)
    assert path.read_bytes() == before


def test_iter_keys_excludes_staging_and_symlinks(tmp_path: Path) -> None:
    storage = ManagedDocumentStorage(tmp_path / "managed", 1024)
    key = "versions/00/evidence.bin"
    path = storage.resolve(key)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    staged = storage.stage(io.BytesIO(b"staged"))
    assert list(storage.iter_managed_keys()) == [key]
    storage.remove_staged(staged)
