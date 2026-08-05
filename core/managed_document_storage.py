"""Streaming, path-safe local storage for immutable controlled-document bytes."""

import hashlib
import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict, Field

from core.document_control import DocumentVersion, IntegrityResult, IntegrityStatus

CHUNK_SIZE = 1024 * 1024


class EmptyManagedFileError(ValueError):
    """Raised when ingestion receives no bytes."""


class ManagedFileTooLargeError(ValueError):
    """Raised when ingestion exceeds the configured byte limit."""


class UnsafeStorageKeyError(ValueError):
    """Raised when an opaque relative key could escape the managed root."""


class ManagedStorageFailureError(RuntimeError):
    """Raised when local managed storage cannot preserve evidence safely."""


class StorageCollisionError(ManagedStorageFailureError):
    """Raised when placement would overwrite an existing managed file."""


class StagedManagedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    path: Path
    byte_size: int = Field(gt=0)
    sha256_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def normalize_display_filename(filename: str) -> str:
    """Normalize untrusted upload text for display, never for path construction."""
    cleaned = "".join(char for char in filename if char >= " " and char != "\x7f")
    cleaned = cleaned.replace("\\", "/").split("/")[-1].strip()
    return cleaned or "document.bin"


class ManagedDocumentStorage:
    """Own immutable managed files beneath one configured local root."""

    def __init__(
        self,
        root: Path,
        max_bytes: int,
        *,
        opener: Callable[[Path, str], BinaryIO] | None = None,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.staging_root = self.root / ".staging"
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self._opener = opener or open

    def stage(self, source: BinaryIO) -> StagedManagedFile:
        """Stream a source into same-filesystem staging while calculating evidence."""
        digest = hashlib.sha256()
        byte_size = 0
        descriptor, raw_path = tempfile.mkstemp(prefix="ingest-", dir=self.staging_root)
        path = Path(raw_path)
        try:
            with os.fdopen(descriptor, "wb") as target:
                while True:
                    chunk = source.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    byte_size += len(chunk)
                    if byte_size > self.max_bytes:
                        raise ManagedFileTooLargeError(
                            f"file exceeds configured maximum of {self.max_bytes} bytes"
                        )
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if byte_size == 0:
                raise EmptyManagedFileError("file must not be empty")
            return StagedManagedFile(
                path=path,
                byte_size=byte_size,
                sha256_digest=digest.hexdigest(),
            )
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def storage_key(document_version_id: str) -> str:
        """Build an opaque, filename-independent relative storage key."""
        opaque = document_version_id.removeprefix("DV-")
        return f"versions/{opaque[:2]}/{document_version_id}.bin"

    def resolve(self, storage_key: str) -> Path:
        """Resolve an opaque key beneath the root or reject it deterministically."""
        candidate = self._raw_path(storage_key).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise UnsafeStorageKeyError("managed-storage key escapes configured root") from exc
        return candidate

    def _raw_path(self, storage_key: str) -> Path:
        """Validate a key lexically without following any filesystem links."""
        pure = PurePosixPath(storage_key)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise UnsafeStorageKeyError("unsafe managed-storage key")
        return self.root / Path(*pure.parts)

    def _has_symlink_component(self, path: Path) -> bool:
        """Detect links without resolving or opening their targets."""
        relative = path.relative_to(self.root)
        candidate = self.root
        for part in relative.parts:
            candidate /= part
            if candidate.is_symlink():
                return True
        return False

    def place(self, staged: StagedManagedFile, storage_key: str) -> Path:
        """Atomically place a staged file without replacing existing evidence."""
        destination = self.resolve(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        reserved = False
        try:
            descriptor = os.open(
                destination,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(descriptor)
            reserved = True
            os.replace(staged.path, destination)
        except FileExistsError as exc:
            raise StorageCollisionError("managed-storage key is already occupied") from exc
        except OSError as exc:
            if reserved:
                destination.unlink(missing_ok=True)
            raise ManagedStorageFailureError("managed file placement failed") from exc
        try:
            placed_size, placed_hash = self._evidence(destination)
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise ManagedStorageFailureError("placed file could not be verified") from exc
        if placed_size != staged.byte_size or placed_hash != staged.sha256_digest:
            destination.unlink(missing_ok=True)
            raise ManagedStorageFailureError("placed file failed evidence verification")
        return destination

    def remove_staged(self, staged: StagedManagedFile) -> None:
        staged.path.unlink(missing_ok=True)

    def remove_owned(self, storage_key: str) -> None:
        """Compensate a failed database operation for its newly placed file."""
        self.resolve(storage_key).unlink(missing_ok=True)

    def open_read(self, storage_key: str) -> BinaryIO:
        """Open a managed file only after safe root-contained resolution."""
        raw_path = self._raw_path(storage_key)
        if self._has_symlink_component(raw_path):
            raise ManagedStorageFailureError("managed file path contains a symbolic link")
        return self._opener(self.resolve(storage_key), "rb")

    def _evidence(self, path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with self._opener(path, "rb") as source:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    def verify(self, version: DocumentVersion) -> IntegrityResult:
        """Read evidence without mutation using deterministic failure precedence."""
        try:
            raw_path = self._raw_path(version.storage_key)
            if self._has_symlink_component(raw_path):
                return self._result(
                    version,
                    IntegrityStatus.UNREADABLE,
                    reason="managed file path contains a symbolic link",
                )
            path = self.resolve(version.storage_key)
        except UnsafeStorageKeyError as exc:
            return self._result(version, IntegrityStatus.UNREADABLE, reason=str(exc))
        if not path.exists():
            return self._result(version, IntegrityStatus.MISSING, reason="managed file is missing")
        try:
            actual_size, actual_hash = self._evidence(path)
        except OSError:
            return self._result(
                version,
                IntegrityStatus.UNREADABLE,
                reason="managed file could not be read",
            )
        if actual_size != version.byte_size:
            return self._result(
                version,
                IntegrityStatus.SIZE_MISMATCH,
                actual_size=actual_size,
                actual_hash=actual_hash,
                reason="stored byte size differs from immutable evidence",
            )
        if actual_hash != version.sha256_digest:
            return self._result(
                version,
                IntegrityStatus.HASH_MISMATCH,
                actual_size=actual_size,
                actual_hash=actual_hash,
                reason="stored SHA-256 differs from immutable evidence",
            )
        return self._result(
            version,
            IntegrityStatus.OK,
            actual_size=actual_size,
            actual_hash=actual_hash,
            reason="stored bytes match immutable evidence",
        )

    @staticmethod
    def _result(
        version: DocumentVersion,
        status: IntegrityStatus,
        *,
        reason: str,
        actual_size: int | None = None,
        actual_hash: str | None = None,
    ) -> IntegrityResult:
        return IntegrityResult(
            document_version_id=version.document_version_id,
            status=status,
            expected_size=version.byte_size,
            actual_size=actual_size,
            expected_sha256=version.sha256_digest,
            actual_sha256=actual_hash,
            reason=reason,
        )

    def iter_managed_keys(self) -> Iterator[str]:
        """Yield final managed keys, excluding the staging area."""
        for storage_key, is_symlink in self._managed_entries():
            if not is_symlink:
                yield storage_key

    def iter_symlink_keys(self) -> Iterator[str]:
        """Yield symlink entries without following or opening their targets."""
        for storage_key, is_symlink in self._managed_entries():
            if is_symlink:
                yield storage_key

    def _managed_entries(self) -> list[tuple[str, bool]]:
        """List managed files and links without traversing linked directories."""
        versions = self.root / "versions"
        if versions.is_symlink():
            return [("versions", True)]
        if not versions.exists():
            return []
        entries: list[tuple[str, bool]] = []
        for directory, directory_names, file_names in os.walk(versions, followlinks=False):
            directory_path = Path(directory)
            for name in list(directory_names):
                path = directory_path / name
                if path.is_symlink():
                    entries.append((path.relative_to(self.root).as_posix(), True))
                    directory_names.remove(name)
            for name in file_names:
                path = directory_path / name
                entries.append((path.relative_to(self.root).as_posix(), path.is_symlink()))
        return sorted(entries)

    def staging_files(self) -> list[Path]:
        """Expose staging residue for diagnostics and deterministic tests."""
        return sorted(path for path in self.staging_root.iterdir() if path.is_file())
