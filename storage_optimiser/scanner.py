import ctypes
import hashlib
import os
import shutil
import sqlite3
import time
from fnmatch import fnmatchcase
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .cache import StorageCache
from .config import drive_for_path
from .models import FileRecord
from .paths import ensure_app_dirs
from .scan_control import stop_requested, write_json

ProgressCallback = Callable[[dict], None]

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
FILE_ATTRIBUTE_OFFLINE = 0x1000
DRIVE_TYPE_NAMES = {
    DRIVE_REMOVABLE: "removable",
    DRIVE_FIXED: "fixed",
    DRIVE_REMOTE: "network",
}


class ScanStopped(Exception):
    pass


def format_bytes(num: int | float) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PB"


def discover_windows_drive_details(include_network: bool = False) -> list[dict[str, str]]:
    drives: list[dict[str, str]] = []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for index in range(26):
            if bitmask & (1 << index):
                letter = chr(65 + index)
                root = f"{letter}:\\"
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
                allowed = drive_type in (DRIVE_REMOVABLE, DRIVE_FIXED)
                if include_network and drive_type == DRIVE_REMOTE:
                    allowed = True
                if allowed:
                    drives.append(
                        {
                            "drive": letter,
                            "root": root,
                            "type": DRIVE_TYPE_NAMES.get(drive_type, "unknown"),
                        }
                    )
    except Exception:
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            if Path(root).exists():
                drives.append({"drive": letter, "root": root, "type": "unknown"})
    return drives


def discover_windows_drives(include_network: bool = False) -> list[str]:
    return [detail["root"] for detail in discover_windows_drive_details(include_network)]


def make_digest(algorithm: str = "sha256"):
    try:
        return hashlib.new(algorithm)
    except (TypeError, ValueError):
        return hashlib.sha256()


def partial_hash_file(path: str, size: int, chunk_size: int, algorithm: str = "sha256") -> str:
    digest = make_digest(algorithm)
    with open(path, "rb") as handle:
        if size <= chunk_size * 3:
            digest.update(handle.read())
        else:
            digest.update(handle.read(chunk_size))
            middle = max(0, (size // 2) - (chunk_size // 2))
            handle.seek(middle)
            digest.update(handle.read(chunk_size))
            handle.seek(max(0, size - chunk_size))
            digest.update(handle.read(chunk_size))
    digest.update(str(size).encode("ascii"))
    return digest.hexdigest()


def full_hash_file(
    path: str,
    chunk_size: int = 1024 * 1024 * 4,
    progress_callback: Callable[[int], None] | None = None,
    stop_callback: Callable[[], None] | None = None,
    progress_bytes: int = 16 * 1024 * 1024,
    progress_seconds: float = 5.0,
    algorithm: str = "sha256",
) -> str:
    digest = make_digest(algorithm)
    bytes_read = 0
    next_emit = max(chunk_size, int(progress_bytes))
    last_emit = time.monotonic()
    with open(path, "rb") as handle:
        while True:
            if stop_callback:
                stop_callback()
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            bytes_read += len(chunk)
            if progress_callback and (
                bytes_read >= next_emit or time.monotonic() - last_emit >= max(0.1, progress_seconds)
            ):
                progress_callback(bytes_read)
                next_emit += max(chunk_size, int(progress_bytes))
                last_emit = time.monotonic()
    return digest.hexdigest()


def normalize_path_text(path: str) -> str:
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def path_for_io(path: str) -> str:
    text = normalize_path_text(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text.lstrip("\\")
    if len(text) >= 240:
        return "\\\\?\\" + text
    return text


class StorageScanner:
    def __init__(
        self,
        config: dict,
        cache: StorageCache,
        progress_callback: ProgressCallback | None = None,
    ):
        self.config = config
        self.cache = cache
        self.progress_callback = progress_callback
        self.progress_path = Path(config["progress_path"])
        self.errors = 0
        self.groups_found = 0
        self.estimated_space_involved = 0
        self.total_root_used_bytes = 0
        self.files_discovered = 0
        self.files_indexed = 0
        self.files_skipped = 0
        self.files_hashed = 0
        self.partial_hash_candidates = 0
        self.full_hash_candidates = 0
        self.cache_hits = 0
        self.current_file = ""
        self.last_error_path = ""
        self.last_error_phase = ""
        self.last_error_message = ""
        self.largest_skipped_size = 0
        self.pending_errors: list[tuple[int, str, str, str, str]] = []
        self.started_monotonic = 0.0
        self.started_at = ""
        self.last_emit_monotonic = 0.0

    def scan(self, mode: str = "test", roots: list[str] | None = None) -> dict:
        ensure_app_dirs()
        self.reset_runtime_state()
        roots = self.resolve_roots(mode, roots)
        self.total_root_used_bytes = self.estimate_roots_used(roots)
        scan_id = self.cache.start_scan(mode, roots)
        stats = {
            "scan_id": scan_id,
            "mode": mode,
            "files_scanned": 0,
            "bytes_scanned": 0,
            "duplicate_groups": 0,
            "duplicate_review_bytes": 0,
            "duplicate_involved_bytes": 0,
            "biggest_duplicate_group_bytes": 0,
            "errors": 0,
        }
        try:
            self.emit(
                scan_id,
                mode,
                "discovering",
                current_drive="",
                current_folder="",
                files_scanned=0,
                bytes_scanned=0,
                root_used_bytes=self.total_root_used_bytes,
                progress_ratio=0.0,
            )
            for root in roots:
                self.raise_if_stopped()
                self.walk_root(root, scan_id, stats)
                self.safe_commit(scan_id, "indexing")
            self.raise_if_stopped()
            self.hash_candidates(scan_id, mode, stats)
            self.emit(
                scan_id,
                mode,
                "writing_summaries",
                files_scanned=stats["files_scanned"],
                bytes_scanned=stats["bytes_scanned"],
                duplicate_groups_found=self.groups_found,
                estimated_space_involved=self.estimated_space_involved,
                progress_ratio=0.99,
            )
            duplicate_stats = self.cache.duplicate_stats(scan_id)
            stats.update(duplicate_stats)
            self.groups_found = int(duplicate_stats["duplicate_groups"])
            self.estimated_space_involved = int(duplicate_stats["duplicate_involved_bytes"])
            try:
                self.cache.optimize()
            except sqlite3.Error as exc:
                self.record_error(scan_id, self.current_file, "database", type(exc).__name__, str(exc))
            status = "complete_with_errors" if self.errors else "complete"
            stats.update(self.runtime_stats(stats))
            self.cache.finish_scan(scan_id, stats, status)
            self.emit(scan_id, mode, status, **self.progress_stats(stats), progress_ratio=1.0)
            return stats
        except KeyboardInterrupt:
            stats.update(self.runtime_stats(stats))
            self.cache.finish_scan(scan_id, stats, "stopped_by_user")
            self.emit(scan_id, mode, "stopped_by_user", **self.progress_stats(stats), progress_ratio=1.0)
            raise
        except ScanStopped:
            stats.update(self.runtime_stats(stats))
            stats["stopped"] = True
            duplicate_stats = self.cache.duplicate_stats(scan_id)
            stats.update(duplicate_stats)
            self.cache.finish_scan(scan_id, stats, "stopped_by_user")
            self.emit(scan_id, mode, "stopped_by_user", **self.progress_stats(stats), progress_ratio=1.0)
            return stats
        except Exception as exc:
            self.record_error(scan_id, self.current_file, "fatal", type(exc).__name__, str(exc))
            stats.update(self.runtime_stats(stats))
            self.cache.finish_scan(scan_id, stats, "failed")
            self.emit(scan_id, mode, "failed", error=str(exc), **self.progress_stats(stats), progress_ratio=1.0)
            raise

    def reset_runtime_state(self) -> None:
        self.errors = 0
        self.groups_found = 0
        self.estimated_space_involved = 0
        self.files_discovered = 0
        self.files_indexed = 0
        self.files_skipped = 0
        self.files_hashed = 0
        self.partial_hash_candidates = 0
        self.full_hash_candidates = 0
        self.cache_hits = 0
        self.current_file = ""
        self.last_error_path = ""
        self.last_error_phase = ""
        self.last_error_message = ""
        self.largest_skipped_size = 0
        self.pending_errors = []
        self.started_monotonic = time.monotonic()
        self.started_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        self.last_emit_monotonic = 0.0

    def roots_for_mode(self, mode: str) -> list[str]:
        if mode == "test":
            return [str(Path(path)) for path in self.config.get("test_paths", [])]
        if mode == "backup_vs_portable":
            return [
                f"{self.config.get('backup_drive', 'B')}:\\",
                f"{self.config.get('portable_drive', 'P')}:\\",
            ]
        if mode not in {"media_only", "full"}:
            raise ValueError("mode must be test, backup_vs_portable, media_only, or full")
        drives = discover_windows_drives(bool(self.config.get("include_network_drives", False)))
        allowlist = [str(item).rstrip(":\\").upper() for item in self.config.get("drive_allowlist", [])]
        if allowlist:
            drives = [root for root in drives if root.rstrip("\\").rstrip(":").upper() in allowlist]
        return drives

    def resolve_roots(self, mode: str, explicit_roots: list[str] | None = None) -> list[str]:
        if explicit_roots is None:
            return self.roots_for_mode(mode)
        roots: list[str] = []
        seen: set[str] = set()
        for root in explicit_roots:
            text = str(root).strip()
            if not text:
                continue
            key = normalize_path_text(text).casefold().rstrip("\\/")
            if key and key not in seen:
                seen.add(key)
                roots.append(text)
        if not roots:
            raise ValueError("At least one scan root is required")
        return roots

    def skip_dir_reason(self, entry: os.DirEntry) -> str | None:
        name = entry.name.casefold()
        path_text = normalize_path_text(entry.path).casefold()
        if not self.config.get("scan_recycle_bin", False) and name == "$recycle.bin".casefold():
            return "Recycle Bin skipped by config"
        if not self.config.get("include_system_folders", False):
            if name in set(self.config.get("excluded_folder_names", [])):
                return "Excluded folder name"
            for fragment in self.config.get("excluded_path_fragments", []):
                if fragment and fragment in path_text:
                    return "Excluded path fragment"
        for pattern in self.config.get("excluded_path_patterns", []):
            if pattern and fnmatchcase(path_text, str(pattern).casefold()):
                return "Excluded path pattern"
        return None

    def skip_file_reason(self, entry: os.DirEntry, stat_result: os.stat_result) -> str | None:
        name = entry.name.casefold()
        path_text = normalize_path_text(entry.path).casefold()
        if name in set(self.config.get("excluded_folder_names", [])):
            return "Excluded system file"
        for fragment in self.config.get("excluded_path_fragments", []):
            if fragment and fragment in path_text:
                return "Excluded path fragment"
        for pattern in self.config.get("excluded_path_patterns", []):
            if pattern and fnmatchcase(path_text, str(pattern).casefold()):
                return "Excluded path pattern"
        attributes = int(getattr(stat_result, "st_file_attributes", 0) or 0)
        if attributes & FILE_ATTRIBUTE_OFFLINE:
            return "Offline cloud placeholder"
        skip_above_mb = int(self.config.get("skip_files_above_mb", 0) or 0)
        if skip_above_mb and int(stat_result.st_size) > skip_above_mb * 1024 * 1024:
            return f"Skipped above configured {skip_above_mb} MB metadata-only limit"
        return None

    def should_scan_file(self, path: str, mode: str) -> bool:
        if mode != "media_only":
            return True
        extension = Path(path).suffix.lower()
        return extension in set(self.config.get("media_only_extensions", []))

    def walk_root(self, root: str, scan_id: int, stats: dict) -> None:
        root_path = Path(root)
        if not root_path.exists():
            self.record_error(scan_id, root, "discovering", "MissingPath", "Path does not exist")
            return
        commit_every = max(1, int(self.config.get("commit_every_files", 250)))
        stack = [str(root_path)]
        while stack:
            self.raise_if_stopped()
            folder = stack.pop()
            current_drive = drive_for_path(folder, self.config)
            self.emit(
                scan_id,
                stats["mode"],
                "indexing",
                current_drive=current_drive,
                current_folder=folder,
                files_scanned=stats["files_scanned"],
                bytes_scanned=stats["bytes_scanned"],
                duplicate_groups_found=self.groups_found,
                estimated_space_involved=self.estimated_space_involved,
                root_used_bytes=self.total_root_used_bytes,
                progress_ratio=self.scan_progress_ratio(stats),
            )
            try:
                with os.scandir(path_for_io(folder)) as entries:
                    for entry in entries:
                        self.raise_if_stopped()
                        self.maybe_emit_heartbeat(scan_id, stats["mode"], "indexing", stats, current_drive, folder)
                        try:
                            if entry.is_symlink() and not self.config.get("follow_symlinks", False):
                                self.record_error(scan_id, normalize_path_text(entry.path), "discovering", "SkippedSymlink", "Symlink skipped by config")
                                continue
                            entry_stat = entry.stat(follow_symlinks=False)
                            attributes = int(getattr(entry_stat, "st_file_attributes", 0) or 0)
                            if attributes & FILE_ATTRIBUTE_REPARSE_POINT and not self.config.get("follow_symlinks", False):
                                self.record_error(scan_id, normalize_path_text(entry.path), "discovering", "SkippedReparsePoint", "Reparse point skipped by config")
                                continue
                            if entry.is_dir(follow_symlinks=bool(self.config.get("follow_symlinks", False))):
                                skip_reason = self.skip_dir_reason(entry)
                                if skip_reason:
                                    self.record_error(scan_id, normalize_path_text(entry.path), "discovering", "SkippedFolder", skip_reason)
                                    continue
                                stack.append(normalize_path_text(entry.path))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            self.files_discovered += 1
                            self.current_file = normalize_path_text(entry.path)
                            if not self.should_scan_file(entry.path, stats["mode"]):
                                self.record_error(scan_id, normalize_path_text(entry.path), "indexing", "SkippedExtension", "File extension skipped by scan mode")
                                continue
                            stat = entry_stat
                            skip_reason = self.skip_file_reason(entry, stat)
                            if skip_reason:
                                self.record_error(
                                    scan_id,
                                    normalize_path_text(entry.path),
                                    "indexing",
                                    "SkippedFile",
                                    skip_reason,
                                    size=int(stat.st_size),
                                )
                                continue
                            path = str(Path(normalize_path_text(entry.path)))
                            partial_hash, full_hash = self.cache.cached_hashes(
                                path, int(stat.st_size), int(stat.st_mtime_ns)
                            )
                            if partial_hash or full_hash:
                                self.cache_hits += 1
                            modified_at = datetime.fromtimestamp(
                                stat.st_mtime, tz=timezone.utc
                            ).astimezone().isoformat(timespec="seconds")
                            record = FileRecord(
                                path=path,
                                folder=str(Path(path).parent),
                                drive=drive_for_path(path, self.config),
                                filename=Path(path).name,
                                extension=Path(path).suffix.lower(),
                                size=int(stat.st_size),
                                mtime_ns=int(stat.st_mtime_ns),
                                modified_at=modified_at,
                                partial_hash=partial_hash,
                                full_hash=full_hash,
                                hardlink_key=self.hardlink_key(stat),
                                hardlink_count=int(getattr(stat, "st_nlink", 1) or 1),
                            )
                            self.cache.upsert_file(scan_id, record)
                            stats["files_scanned"] += 1
                            self.files_indexed += 1
                            stats["bytes_scanned"] += int(stat.st_size)
                            if stats["files_scanned"] % commit_every == 0:
                                self.safe_commit(scan_id, "indexing")
                                self.emit(
                                    scan_id,
                                    stats["mode"],
                                    "indexing",
                                    current_drive=record.drive,
                                    current_folder=record.folder,
                                    files_scanned=stats["files_scanned"],
                                    bytes_scanned=stats["bytes_scanned"],
                                    duplicate_groups_found=self.groups_found,
                                    estimated_space_involved=self.estimated_space_involved,
                                    root_used_bytes=self.total_root_used_bytes,
                                    progress_ratio=self.scan_progress_ratio(stats),
                                )
                        except (OSError, UnicodeError, ValueError, sqlite3.Error) as exc:
                            self.record_error(scan_id, entry.path, "indexing", type(exc).__name__, str(exc))
            except (OSError, UnicodeError, ValueError) as exc:
                self.record_error(scan_id, folder, "discovering", type(exc).__name__, str(exc))

    def hash_candidates(self, scan_id: int, mode: str, stats: dict) -> None:
        total = max(self.cache.candidate_size_count(scan_id), 1)
        chunk_size = int(self.config.get("partial_hash_bytes", 1024 * 1024))
        hash_commit_every = max(1, int(self.config.get("hash_commit_every_files", 250)))
        self.partial_hash_candidates = self.cache.candidate_file_count(scan_id)

        for index, size in enumerate(self.cache.iter_candidate_sizes(scan_id), start=1):
            self.raise_if_stopped()
            self.emit(
                scan_id,
                mode,
                "partial_hash",
                current_drive="",
                current_folder=f"size group {format_bytes(size)}",
                files_scanned=stats["files_scanned"],
                bytes_scanned=stats["bytes_scanned"],
                duplicate_groups_found=self.groups_found,
                estimated_space_involved=self.estimated_space_involved,
                progress_current=index,
                progress_total=total,
                progress_ratio=min(0.80, 0.55 + (index / total) * 0.25),
            )
            processed = 0
            for record in self.cache.iter_files_for_size(scan_id, size):
                self.raise_if_stopped()
                if record.partial_hash:
                    continue
                self.current_file = record.path
                try:
                    record.partial_hash = partial_hash_file(
                        path_for_io(record.path),
                        record.size,
                        chunk_size,
                        algorithm=str(self.config.get("hash_algorithm", "sha256")),
                    )
                    self.cache.update_hashes(record.path, partial_hash=record.partial_hash)
                    self.files_hashed += 1
                except (OSError, UnicodeError, ValueError, sqlite3.Error) as exc:
                    self.record_error(
                        scan_id,
                        record.path,
                        "partial_hash",
                        type(exc).__name__,
                        str(exc),
                        size=record.size,
                    )
                processed += 1
                if processed % hash_commit_every == 0:
                    self.safe_commit(scan_id, "partial_hash")
            self.safe_commit(scan_id, "partial_hash")

        self.full_hash_candidates = self.cache.partial_hash_candidate_file_count(scan_id)
        full_hash_limit_mb = int(self.config.get("full_hash_max_mb", 0) or 0)
        full_hash_limit = full_hash_limit_mb * 1024 * 1024
        allow_large_full_hash = bool(self.config.get("full_hash_large_files", True))
        hash_progress_bytes = int(self.config.get("hash_progress_bytes", 16 * 1024 * 1024))

        for index, size in enumerate(self.cache.iter_candidate_sizes(scan_id), start=1):
            for partial_hash in self.cache.iter_candidate_partial_hashes(scan_id, size):
                processed = 0
                for record in self.cache.iter_files_for_partial_hash(scan_id, size, partial_hash):
                    self.raise_if_stopped()
                    if record.full_hash:
                        continue
                    self.current_file = record.path
                    if full_hash_limit and record.size > full_hash_limit and not allow_large_full_hash:
                        self.record_error(
                            scan_id,
                            record.path,
                            "full_hash",
                            "SkippedLargeFile",
                            f"Full hash skipped above configured {full_hash_limit_mb} MB limit",
                            size=record.size,
                        )
                        continue
                    try:
                        base_ratio = min(0.98, 0.80 + (index / total) * 0.18)

                        def emit_hash_progress(bytes_read: int, record=record, base_ratio=base_ratio) -> None:
                            self.emit(
                                scan_id,
                                mode,
                                "full_hash",
                                current_drive=record.drive,
                                current_folder=record.folder,
                                current_file=f"{record.filename} ({format_bytes(bytes_read)} of {format_bytes(record.size)})",
                                files_scanned=stats["files_scanned"],
                                bytes_scanned=stats["bytes_scanned"],
                                duplicate_groups_found=self.groups_found,
                                estimated_space_involved=self.estimated_space_involved,
                                progress_current=index,
                                progress_total=total,
                                progress_ratio=base_ratio,
                            )

                        record.full_hash = full_hash_file(
                            path_for_io(record.path),
                            progress_callback=emit_hash_progress,
                            stop_callback=self.raise_if_stopped,
                            progress_bytes=hash_progress_bytes,
                            progress_seconds=float(self.config.get("heartbeat_seconds", 5)),
                            algorithm=str(self.config.get("hash_algorithm", "sha256")),
                        )
                        self.cache.update_hashes(record.path, full_hash=record.full_hash)
                        self.files_hashed += 1
                    except (OSError, UnicodeError, ValueError, sqlite3.Error) as exc:
                        self.record_error(
                            scan_id,
                            record.path,
                            "full_hash",
                            type(exc).__name__,
                            str(exc),
                            size=record.size,
                        )
                    processed += 1
                    if processed % hash_commit_every == 0:
                        self.safe_commit(scan_id, "full_hash")
                self.safe_commit(scan_id, "full_hash")

    def record_error(
        self,
        scan_id: int,
        path: str,
        phase: str,
        error_type: str,
        message: str,
        size: int = 0,
    ) -> None:
        self.errors += 1
        self.files_skipped += 1
        self.last_error_path = str(path)
        self.last_error_phase = phase
        self.last_error_message = str(message)
        self.largest_skipped_size = max(self.largest_skipped_size, int(size or 0))
        try:
            self.cache.add_error(scan_id, path, error_type, message, phase=phase)
        except sqlite3.Error:
            self.pending_errors.append((scan_id, str(path), phase, error_type, str(message)))
        if self.errors % 50 == 0:
            self.safe_commit(scan_id, "database")

    def flush_pending_errors(self) -> None:
        while self.pending_errors:
            scan_id, path, phase, error_type, message = self.pending_errors[0]
            try:
                self.cache.add_error(scan_id, path, error_type, message, phase=phase)
            except sqlite3.Error:
                return
            self.pending_errors.pop(0)

    def safe_commit(self, scan_id: int, phase: str) -> bool:
        try:
            self.cache.commit()
            self.flush_pending_errors()
            return True
        except sqlite3.Error as exc:
            self.record_error(scan_id, self.current_file, phase, type(exc).__name__, str(exc))
            return False

    def emit(self, scan_id: int, mode: str, phase: str, **fields) -> None:
        payload = {
            "scan_id": scan_id,
            "mode": mode,
            "phase": phase,
            "started_at": fields.pop("started_at", self.started_at),
            "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "heartbeat_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "current_drive": fields.pop("current_drive", ""),
            "current_folder": fields.pop("current_folder", ""),
            "current_file": fields.pop("current_file", self.current_file),
            "files_scanned": fields.pop("files_scanned", 0),
            "files_discovered": fields.pop("files_discovered", self.files_discovered),
            "files_indexed": fields.pop("files_indexed", self.files_indexed),
            "files_hashed": fields.pop("files_hashed", self.files_hashed),
            "files_skipped": fields.pop("files_skipped", self.files_skipped),
            "errors": fields.pop("errors", self.errors),
            "last_error_path": fields.pop("last_error_path", self.last_error_path),
            "last_error_phase": fields.pop("last_error_phase", self.last_error_phase),
            "last_error_message": fields.pop("last_error_message", self.last_error_message),
            "bytes_scanned": fields.pop("bytes_scanned", 0),
            "duplicate_groups_found": fields.pop("duplicate_groups_found", 0),
            "estimated_space_involved": fields.pop("estimated_space_involved", 0),
            "progress_ratio": fields.pop("progress_ratio", None),
        }
        payload.update(fields)
        write_json(self.progress_path, payload)
        self.last_emit_monotonic = time.monotonic()
        if self.progress_callback:
            try:
                self.progress_callback(payload)
            except Exception:
                pass

    def maybe_emit_heartbeat(
        self,
        scan_id: int,
        mode: str,
        phase: str,
        stats: dict,
        drive: str,
        folder: str,
    ) -> None:
        interval = max(1.0, float(self.config.get("heartbeat_seconds", 5)))
        if time.monotonic() - self.last_emit_monotonic < interval:
            return
        self.emit(
            scan_id,
            mode,
            phase,
            current_drive=drive,
            current_folder=folder,
            files_scanned=stats["files_scanned"],
            bytes_scanned=stats["bytes_scanned"],
            duplicate_groups_found=self.groups_found,
            estimated_space_involved=self.estimated_space_involved,
            root_used_bytes=self.total_root_used_bytes,
            progress_ratio=self.scan_progress_ratio(stats),
        )

    def progress_stats(self, stats: dict) -> dict:
        data = {key: value for key, value in stats.items() if key not in {"scan_id", "mode"}}
        if "duplicate_groups" in data:
            data["duplicate_groups_found"] = data["duplicate_groups"]
        if "duplicate_involved_bytes" in data:
            data["estimated_space_involved"] = data["duplicate_involved_bytes"]
        data.update(self.runtime_stats(stats))
        return data

    def runtime_stats(self, stats: dict) -> dict:
        elapsed = max(0.001, time.monotonic() - self.started_monotonic)
        files_scanned = int(stats.get("files_scanned") or 0)
        bytes_scanned = int(stats.get("bytes_scanned") or 0)
        return {
            "errors": self.errors,
            "files_discovered": self.files_discovered,
            "files_indexed": self.files_indexed,
            "files_skipped": self.files_skipped,
            "files_hashed": self.files_hashed,
            "partial_hash_candidates": self.partial_hash_candidates,
            "full_hash_candidates": self.full_hash_candidates,
            "cache_hits": self.cache_hits,
            "last_error_path": self.last_error_path,
            "last_error_phase": self.last_error_phase,
            "last_error_message": self.last_error_message,
            "largest_skipped_size": self.largest_skipped_size,
            "duration_seconds": elapsed,
            "avg_files_per_second": files_scanned / elapsed,
            "avg_megabytes_per_second": (bytes_scanned / (1024 * 1024)) / elapsed,
        }

    def raise_if_stopped(self) -> None:
        if stop_requested(self.config):
            self.cache.commit()
            raise ScanStopped()

    def scan_progress_ratio(self, stats: dict) -> float | None:
        if self.total_root_used_bytes <= 0:
            return None
        ratio = int(stats.get("bytes_scanned") or 0) / self.total_root_used_bytes
        return min(0.55, max(0.0, ratio * 0.55))

    @staticmethod
    def estimate_roots_used(roots: list[str]) -> int:
        total = 0
        for root in roots:
            try:
                total += int(shutil.disk_usage(root).used)
            except OSError:
                continue
        return total

    @staticmethod
    def hardlink_key(stat_result: os.stat_result) -> str | None:
        count = int(getattr(stat_result, "st_nlink", 1) or 1)
        if count <= 1:
            return None
        device = getattr(stat_result, "st_dev", None)
        inode = getattr(stat_result, "st_ino", None)
        if device is None or inode is None:
            return None
        return f"{device}:{inode}"
