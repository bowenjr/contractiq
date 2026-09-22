"""Path-safe managed storage for immutable Bid package release snapshots."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict

from core.bid_package_intake import IntakeInbox, IntakeInboxFolder, PreviewFile, ReleasePreview

CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_PATH_DEPTH = 32

_SUPPORTED_INTAKE_EXTENSIONS = frozenset(
    {
        ".7z",
        ".bmp",
        ".csv",
        ".doc",
        ".docm",
        ".docx",
        ".dwg",
        ".dxf",
        ".eml",
        ".gif",
        ".gz",
        ".jpeg",
        ".jpg",
        ".msg",
        ".pdf",
        ".png",
        ".rar",
        ".rtf",
        ".tar",
        ".tif",
        ".tiff",
        ".txt",
        ".xls",
        ".xlsb",
        ".xlsm",
        ".xlsx",
        ".xz",
        ".zip",
    }
)


class IntakeStorageError(RuntimeError):
    """Base class for managed-intake storage failures."""


class UnsafeIntakeSourceError(IntakeStorageError):
    """Raised when an intake source violates the configured trust boundary."""


class IntakeLimitError(IntakeStorageError):
    """Raised when a bounded intake limit is exceeded."""


class SourceChangedError(IntakeStorageError):
    """Raised when source evidence no longer matches the confirmed preview."""


class IntakePublicationError(IntakeStorageError):
    """Raised when staged evidence cannot be published atomically."""


class StagedIntakeFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file_id: str
    preview: PreviewFile
    opaque_filename: str
    staged_path: Path


class StagedRelease(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, frozen=True)

    release_id: str
    operation_id: str
    stage_directory: Path
    final_relative_directory: str
    files: tuple[StagedIntakeFile, ...]


def _media_type(path: Path) -> str | None:
    value, _encoding = mimetypes.guess_type(path.name, strict=False)
    return value


def is_supported_intake_file(preview_file: PreviewFile) -> bool:
    """Return whether a regular file makes an inbox package selectable."""
    if preview_file.extension in _SUPPORTED_INTAKE_EXTENSIONS:
        return True
    media_type = preview_file.detected_media_type or ""
    return media_type.startswith(("text/", "image/"))


def parse_intake_roots(value: str, *, variable_name: str) -> dict[str, Path]:
    """Parse one absolute intake root or a JSON object of named absolute roots."""
    raw = value.strip()
    if not raw:
        raise ValueError(f"{variable_name} must not be empty")
    if raw.startswith("{"):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{variable_name} must be a valid JSON object") from exc
        if not isinstance(decoded, dict) or not decoded:
            raise ValueError(f"{variable_name} must be a non-empty JSON object")
        roots: dict[str, Path] = {}
        for alias, path_value in decoded.items():
            if not isinstance(alias, str) or not isinstance(path_value, str):
                raise ValueError(f"{variable_name} JSON values must be string paths")
            candidate = Path(path_value)
            if not candidate.is_absolute():
                raise ValueError(f"{variable_name} paths must be absolute")
            roots[alias] = candidate
        return roots
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError(f"{variable_name} must be an absolute path or JSON object")
    return {"inbox_1": candidate}


class BidPackageStorage:
    """One-time COPY_MANAGED_ONLY intake beneath a distinct configured root."""

    def __init__(
        self,
        managed_root: Path,
        source_roots: Mapping[str, Path],
        *,
        max_outer_files: int = 1_000,
        max_total_bytes: int = 1_073_741_824,
        max_file_bytes: int = 262_144_000,
        max_relative_path_length: int = 2_048,
        max_path_depth: int = DEFAULT_MAX_PATH_DEPTH,
        quarantine_days: int = 7,
        opener: Callable[[Path, str], BinaryIO] | None = None,
    ) -> None:
        if not source_roots:
            raise ValueError("at least one allowlisted source root is required")
        if (
            min(
                max_outer_files,
                max_total_bytes,
                max_file_bytes,
                max_relative_path_length,
                max_path_depth,
                quarantine_days,
            )
            < 1
        ):
            raise ValueError("intake limits must be positive")
        self.managed_root = managed_root.resolve()
        self.source_roots: dict[str, Path] = {}
        for alias, untrusted_root in source_roots.items():
            normalized_alias = alias.strip()
            if not normalized_alias or "/" in normalized_alias or "\\" in normalized_alias:
                raise ValueError("source source-root alias is invalid")
            if untrusted_root.is_symlink():
                raise UnsafeIntakeSourceError("configured source root cannot be a symbolic link")
            try:
                resolved = untrusted_root.resolve(strict=True)
            except OSError as exc:
                raise UnsafeIntakeSourceError(
                    "configured intake root is unavailable; "
                    "verify its configuration and permissions"
                ) from exc
            if not resolved.is_dir():
                raise UnsafeIntakeSourceError("configured source root must be a directory")
            self.source_roots[normalized_alias] = resolved
        self.max_outer_files = max_outer_files
        self.max_total_bytes = max_total_bytes
        self.max_file_bytes = max_file_bytes
        self.max_relative_path_length = max_relative_path_length
        self.max_path_depth = max_path_depth
        self.quarantine_days = quarantine_days
        self.staging_root = self.managed_root / ".staging"
        self.quarantine_root = self.managed_root / ".quarantine"
        self.releases_root = self.managed_root / "releases"
        for directory in (self.staging_root, self.quarantine_root, self.releases_root):
            directory.mkdir(parents=True, exist_ok=True)
        self._opener = opener or open

    def source_choices(self) -> tuple[tuple[str, str], ...]:
        """Return opaque aliases and non-sensitive display labels for the browser."""
        return tuple(
            (alias, f"Configured intake location {index}")
            for index, alias in enumerate(self.source_roots, 1)
        )

    def _source_root(self, alias: str) -> Path:
        try:
            return self.source_roots[alias]
        except KeyError as exc:
            raise UnsafeIntakeSourceError("source root is not allowlisted") from exc

    def _selected_source_root(self, alias: str, source_folder: str | None) -> Path:
        """Resolve only one non-symlink immediate child beneath an allowlisted inbox."""
        root = self._source_root(alias)
        if source_folder is None:
            return root
        normalized = source_folder.strip()
        pure = PurePosixPath(normalized)
        if (
            not normalized
            or pure.is_absolute()
            or len(pure.parts) != 1
            or pure.parts[0] in {"", ".", ".."}
            or "\\" in normalized
        ):
            raise UnsafeIntakeSourceError("select one available intake folder")
        candidate = root / pure.parts[0]
        self._assert_no_symlink_components(root, candidate)
        if candidate.is_symlink() or not candidate.exists() or not candidate.is_dir():
            raise UnsafeIntakeSourceError("selected intake folder is unavailable")
        return candidate

    @staticmethod
    def _assert_no_symlink_components(root: Path, path: Path) -> None:
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise UnsafeIntakeSourceError("source path escapes its allowlisted root") from exc
        candidate = root
        if candidate.is_symlink():
            raise UnsafeIntakeSourceError("source root cannot be a symbolic link")
        for part in relative.parts:
            candidate /= part
            if candidate.is_symlink():
                raise UnsafeIntakeSourceError("symbolic links are not accepted during intake")

    @staticmethod
    def _open_flags() -> int:
        return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)

    def _hash_regular(self, root: Path, path: Path) -> tuple[int, str]:
        self._assert_no_symlink_components(root, path)
        digest = hashlib.sha256()
        size = 0
        descriptor = os.open(path, self._open_flags())
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise UnsafeIntakeSourceError("only regular files can be registered")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                while chunk := source.read(CHUNK_SIZE):
                    size += len(chunk)
                    digest.update(chunk)
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise SourceChangedError("source changed while evidence was being read")
        finally:
            os.close(descriptor)
        return size, digest.hexdigest()

    def _walk(self, root: Path) -> list[Path]:
        files: list[Path] = []
        pending = [root]
        while pending:
            directory = pending.pop()
            self._assert_no_symlink_components(root, directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        raise UnsafeIntakeSourceError(
                            "symbolic links are not accepted during intake"
                        )
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        if len(path.relative_to(root).parts) > self.max_path_depth:
                            raise IntakeLimitError(
                                "source hierarchy exceeds the configured path-depth limit"
                            )
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(path)
                    else:
                        raise UnsafeIntakeSourceError(
                            "only regular files and directories are accepted"
                        )
                    if len(files) > self.max_outer_files:
                        raise IntakeLimitError(
                            f"release exceeds the {self.max_outer_files} outer-file limit"
                        )
        return sorted(files, key=lambda item: item.relative_to(root).as_posix())

    def preview(self, source_root_alias: str, source_folder: str | None = None) -> ReleasePreview:
        """Read and hash an allowlisted landing location without any mutation."""
        root = self._selected_source_root(source_root_alias, source_folder)
        files: list[PreviewFile] = []
        total_bytes = 0
        for path in self._walk(root):
            relative = path.relative_to(root).as_posix()
            pure = PurePosixPath(relative)
            if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
                raise UnsafeIntakeSourceError("source relative path is unsafe")
            if len(relative) > self.max_relative_path_length:
                raise IntakeLimitError("source relative path exceeds the configured limit")
            if len(pure.parts) > self.max_path_depth:
                raise IntakeLimitError("source hierarchy exceeds the configured path-depth limit")
            size, digest = self._hash_regular(root, path)
            if size > self.max_file_bytes:
                raise IntakeLimitError(
                    f"file exceeds the {self.max_file_bytes}-byte individual limit"
                )
            total_bytes += size
            if total_bytes > self.max_total_bytes:
                raise IntakeLimitError(
                    f"release exceeds the {self.max_total_bytes}-byte aggregate limit"
                )
            files.append(
                PreviewFile(
                    original_relative_path=relative,
                    original_filename=path.name,
                    extension=path.suffix.lower(),
                    detected_media_type=_media_type(path),
                    byte_size=size,
                    sha256=digest,
                )
            )
        canonical = json.dumps(
            [item.model_dump(mode="json") for item in files],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return ReleasePreview(
            source_root_alias=source_root_alias,
            source_folder=source_folder,
            source_fingerprint=hashlib.sha256(canonical).hexdigest(),
            files=tuple(files),
            total_bytes=total_bytes,
        )

    def inbox(self, source_root_alias: str) -> IntakeInbox:
        """List safe immediate child folders without creating evidence or audit rows."""
        root = self._source_root(source_root_alias)
        folders: list[IntakeInboxFolder] = []
        direct_file_count = 0
        rejected_entries = 0
        try:
            entries_context = os.scandir(root)
        except OSError:
            return IntakeInbox(
                source_root_alias=source_root_alias,
                display_name=(
                    "Configured intake inbox "
                    f"{list(self.source_roots).index(source_root_alias) + 1}"
                ),
                folders=(),
                direct_file_count=0,
                warning=(
                    "Configured intake inbox is unavailable; verify configuration and permissions."
                ),
            )
        with entries_context as entries:
            for entry in sorted(entries, key=lambda item: item.name.casefold()):
                path = Path(entry.path)
                if entry.is_symlink():
                    rejected_entries += 1
                    continue
                if entry.is_file(follow_symlinks=False):
                    direct_file_count += 1
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    rejected_entries += 1
                    continue
                try:
                    preview = self.preview(source_root_alias, entry.name)
                    file_paths = self._walk(path)
                    last_modified = max(
                        [path.stat().st_mtime, *(item.stat().st_mtime for item in file_paths)]
                    )
                    supported_file_count = sum(
                        is_supported_intake_file(file) for file in preview.files
                    )
                    folder_warning: str | None = None
                    if preview.file_count == 0:
                        folder_warning = "No files found in this package folder."
                    elif supported_file_count == 0:
                        folder_warning = "No supported files found in this package folder."
                    folders.append(
                        IntakeInboxFolder(
                            source_root_alias=source_root_alias,
                            folder_name=entry.name,
                            display_name=entry.name,
                            file_count=preview.file_count,
                            supported_file_count=supported_file_count,
                            total_bytes=preview.total_bytes,
                            last_modified_at=datetime.fromtimestamp(last_modified, tz=UTC),
                            source_fingerprint=preview.source_fingerprint,
                            warning=folder_warning,
                        )
                    )
                except (IntakeStorageError, OSError) as exc:
                    folders.append(
                        IntakeInboxFolder(
                            source_root_alias=source_root_alias,
                            folder_name=entry.name,
                            display_name=entry.name,
                            file_count=0,
                            supported_file_count=0,
                            total_bytes=0,
                            last_modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                            warning=str(exc),
                        )
                    )
        warning: str | None = None
        if direct_file_count:
            warning = (
                "This configured inbox contains files directly. Create one package folder "
                "per received package, then refresh."
            )
        elif rejected_entries:
            warning = "Some unsafe inbox entries are unavailable for intake."
        return IntakeInbox(
            source_root_alias=source_root_alias,
            display_name=(
                f"Configured intake inbox {list(self.source_roots).index(source_root_alias) + 1}"
            ),
            folders=tuple(folders),
            direct_file_count=direct_file_count,
            warning=warning,
        )

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def stage(
        self,
        preview: ReleasePreview,
        release_id: str,
        operation_id: str,
        file_ids: Mapping[str, str],
    ) -> StagedRelease:
        """Copy expected source bytes to a same-filesystem, fsynced staging tree."""
        current = self.preview(preview.source_root_alias, preview.source_folder)
        if current.source_fingerprint != preview.source_fingerprint:
            raise SourceChangedError("source changed after preview; preview the release again")
        root = self._selected_source_root(preview.source_root_alias, preview.source_folder)
        stage_directory = Path(tempfile.mkdtemp(prefix="release-", dir=self.staging_root))
        originals = stage_directory / "originals"
        originals.mkdir()
        staged_files: list[StagedIntakeFile] = []
        try:
            for expected in current.files:
                file_id = file_ids[expected.original_relative_path]
                opaque_filename = f"{file_id}.bin"
                destination = originals / opaque_filename
                source_path = root / Path(*PurePosixPath(expected.original_relative_path).parts)
                self._assert_no_symlink_components(root, source_path)
                source_digest = hashlib.sha256()
                source_size = 0
                source_fd = os.open(source_path, self._open_flags())
                try:
                    before = os.fstat(source_fd)
                    if not stat.S_ISREG(before.st_mode):
                        raise UnsafeIntakeSourceError("intake source is no longer a regular file")
                    with (
                        os.fdopen(source_fd, "rb", closefd=False) as source,
                        self._opener(destination, "xb") as target,
                    ):
                        while chunk := source.read(CHUNK_SIZE):
                            source_size += len(chunk)
                            source_digest.update(chunk)
                            target.write(chunk)
                        target.flush()
                        os.fsync(target.fileno())
                    after = os.fstat(source_fd)
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    ):
                        raise SourceChangedError("source changed during managed copy")
                finally:
                    os.close(source_fd)
                managed_size, managed_hash = self._hash_regular(originals, destination)
                if (
                    source_size != expected.byte_size
                    or source_digest.hexdigest() != expected.sha256
                    or managed_size != expected.byte_size
                    or managed_hash != expected.sha256
                ):
                    raise SourceChangedError(
                        "source and managed copy evidence do not match preview"
                    )
                staged_files.append(
                    StagedIntakeFile(
                        file_id=file_id,
                        preview=expected,
                        opaque_filename=opaque_filename,
                        staged_path=destination,
                    )
                )
            self._fsync_directory(originals)
            self._fsync_directory(stage_directory)
            return StagedRelease(
                release_id=release_id,
                operation_id=operation_id,
                stage_directory=stage_directory,
                final_relative_directory=f"releases/{release_id}",
                files=tuple(staged_files),
            )
        except Exception:
            self.quarantine(stage_directory, operation_id)
            raise

    def publish(self, staged: StagedRelease) -> Path:
        """Atomically publish one fully verified directory without replacement."""
        final = self.managed_root / Path(*PurePosixPath(staged.final_relative_directory).parts)
        try:
            final.relative_to(self.releases_root)
        except ValueError as exc:
            raise IntakePublicationError("release destination escapes managed intake root") from exc
        if final.exists() or final.is_symlink():
            raise IntakePublicationError("managed release destination is already occupied")
        try:
            os.rename(staged.stage_directory, final)
            self._fsync_directory(self.releases_root)
        except OSError as exc:
            raise IntakePublicationError("managed release publication failed") from exc
        return final

    def remove_published(self, staged: StagedRelease) -> None:
        """Compensate a database rollback for the exact newly published release."""
        final = self.managed_root / Path(*PurePosixPath(staged.final_relative_directory).parts)
        if final.parent != self.releases_root or final.is_symlink():
            raise IntakePublicationError("refusing unsafe managed release compensation")
        if final.exists():
            shutil.rmtree(final)
            self._fsync_directory(self.releases_root)

    def open_read(self, managed_storage_key: str) -> BinaryIO:
        """Open one opaque managed original without exposing a static path."""
        pure = PurePosixPath(managed_storage_key)
        if (
            pure.is_absolute()
            or len(pure.parts) != 4
            or pure.parts[0] != "releases"
            or pure.parts[2] != "originals"
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise IntakePublicationError("unsafe managed intake storage key")
        path = self.managed_root / Path(*pure.parts)
        self._assert_no_symlink_components(self.managed_root, path)
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(self.releases_root)
        except ValueError as exc:
            raise IntakePublicationError("managed original escapes intake storage") from exc
        return self._opener(resolved, "rb")

    def discard_staged(self, staged: StagedRelease) -> None:
        if staged.stage_directory.exists():
            shutil.rmtree(staged.stage_directory)

    def quarantine(self, directory: Path, operation_id: str) -> Path | None:
        """Retain failed staging evidence for bounded local diagnosis."""
        if not directory.exists():
            return None
        if directory.parent != self.staging_root or directory.is_symlink():
            raise IntakePublicationError("refusing to quarantine an unsafe staging path")
        safe_operation = hashlib.sha256(operation_id.encode()).hexdigest()[:16]
        target = self.quarantine_root / (
            f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{safe_operation}"
        )
        os.rename(directory, target)
        self._fsync_directory(self.quarantine_root)
        return target

    def prune_quarantine(self, now: datetime | None = None) -> int:
        """Remove only quarantined directories older than the configured retention."""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        cutoff = current - timedelta(days=self.quarantine_days)
        removed = 0
        for path in self.quarantine_root.iterdir():
            if path.is_symlink() or not path.is_dir():
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if modified < cutoff:
                shutil.rmtree(path)
                removed += 1
        if removed:
            self._fsync_directory(self.quarantine_root)
        return removed

    def recover(self, registered_release_directories: Iterable[str]) -> tuple[int, int]:
        """Quarantine interrupted staging and unreferenced published directories."""
        registered = set(registered_release_directories)
        staged_count = 0
        orphan_count = 0
        for path in tuple(self.staging_root.iterdir()):
            if path.is_dir() and not path.is_symlink():
                self.quarantine(path, path.name)
                staged_count += 1
        for path in tuple(self.releases_root.iterdir()):
            relative = f"releases/{path.name}"
            if relative not in registered and path.is_dir() and not path.is_symlink():
                target = self.quarantine_root / (
                    f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-orphan-"
                    f"{hashlib.sha256(path.name.encode()).hexdigest()[:12]}"
                )
                os.rename(path, target)
                orphan_count += 1
        if orphan_count:
            self._fsync_directory(self.releases_root)
            self._fsync_directory(self.quarantine_root)
        self.prune_quarantine()
        return staged_count, orphan_count


__all__ = [
    "BidPackageStorage",
    "IntakeLimitError",
    "IntakePublicationError",
    "IntakeStorageError",
    "SourceChangedError",
    "StagedIntakeFile",
    "StagedRelease",
    "UnsafeIntakeSourceError",
]
