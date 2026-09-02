from dataclasses import dataclass


@dataclass
class FileRecord:
    path: str
    folder: str
    drive: str
    filename: str
    extension: str
    size: int
    mtime_ns: int
    modified_at: str
    partial_hash: str | None = None
    full_hash: str | None = None
    hardlink_key: str | None = None
    hardlink_count: int = 1


@dataclass
class ScanStats:
    scan_id: int
    mode: str
    files_scanned: int = 0
    bytes_scanned: int = 0
    duplicate_groups: int = 0
    duplicate_review_bytes: int = 0
    duplicate_involved_bytes: int = 0
    biggest_duplicate_group_bytes: int = 0
    errors: int = 0
