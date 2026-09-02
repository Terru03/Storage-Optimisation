import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import FileRecord


class StorageCache:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=5)
        self.conn.row_factory = sqlite3.Row
        self._execute("PRAGMA journal_mode=WAL")
        self._execute("PRAGMA synchronous=NORMAL")
        self._execute("PRAGMA busy_timeout=5000")
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self._run_with_retry(lambda: self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                root_paths TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                files_scanned INTEGER DEFAULT 0,
                bytes_scanned INTEGER DEFAULT 0,
                duplicate_groups INTEGER DEFAULT 0,
                duplicate_review_bytes INTEGER DEFAULT 0,
                duplicate_involved_bytes INTEGER DEFAULT 0,
                biggest_duplicate_group_bytes INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                files_discovered INTEGER DEFAULT 0,
                files_skipped INTEGER DEFAULT 0,
                files_hashed INTEGER DEFAULT 0,
                partial_hash_candidates INTEGER DEFAULT 0,
                full_hash_candidates INTEGER DEFAULT 0,
                cache_hits INTEGER DEFAULT 0,
                last_error_path TEXT DEFAULT '',
                last_error_phase TEXT DEFAULT '',
                last_error_message TEXT DEFAULT '',
                largest_skipped_size INTEGER DEFAULT 0,
                duration_seconds REAL DEFAULT 0,
                avg_files_per_second REAL DEFAULT 0,
                avg_megabytes_per_second REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                folder TEXT NOT NULL,
                drive TEXT NOT NULL,
                filename TEXT NOT NULL,
                extension TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                modified_at TEXT NOT NULL,
                partial_hash TEXT,
                full_hash TEXT,
                hardlink_key TEXT,
                hardlink_count INTEGER DEFAULT 1,
                last_seen_scan_id INTEGER NOT NULL,
                scan_date TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_files_scan_size
                ON files(last_seen_scan_id, size);
            CREATE INDEX IF NOT EXISTS idx_files_scan
                ON files(last_seen_scan_id);
            CREATE INDEX IF NOT EXISTS idx_files_scan_partial
                ON files(last_seen_scan_id, size, partial_hash);
            CREATE INDEX IF NOT EXISTS idx_files_scan_full
                ON files(last_seen_scan_id, full_hash);
            CREATE INDEX IF NOT EXISTS idx_files_scan_size_full
                ON files(last_seen_scan_id, size, full_hash);
            CREATE INDEX IF NOT EXISTS idx_files_drive
                ON files(last_seen_scan_id, drive);
            CREATE INDEX IF NOT EXISTS idx_files_scan_extension
                ON files(last_seen_scan_id, extension);
            CREATE INDEX IF NOT EXISTS idx_files_scan_modified
                ON files(last_seen_scan_id, modified_at);
            CREATE INDEX IF NOT EXISTS idx_files_scan_folder
                ON files(last_seen_scan_id, folder);

            CREATE TABLE IF NOT EXISTS scan_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT '',
                error_type TEXT NOT NULL,
                error_message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        ))
        self.ensure_column("files", "hardlink_key", "TEXT")
        self.ensure_column("files", "hardlink_count", "INTEGER DEFAULT 1")
        self.ensure_column("scan_errors", "phase", "TEXT DEFAULT ''")
        for column, column_type in {
            "files_discovered": "INTEGER DEFAULT 0",
            "files_skipped": "INTEGER DEFAULT 0",
            "files_hashed": "INTEGER DEFAULT 0",
            "partial_hash_candidates": "INTEGER DEFAULT 0",
            "full_hash_candidates": "INTEGER DEFAULT 0",
            "cache_hits": "INTEGER DEFAULT 0",
            "last_error_path": "TEXT DEFAULT ''",
            "last_error_phase": "TEXT DEFAULT ''",
            "last_error_message": "TEXT DEFAULT ''",
            "largest_skipped_size": "INTEGER DEFAULT 0",
            "duration_seconds": "REAL DEFAULT 0",
            "avg_files_per_second": "REAL DEFAULT 0",
            "avg_megabytes_per_second": "REAL DEFAULT 0",
        }.items():
            self.ensure_column("scans", column, column_type)
        self._commit()

    def _run_with_retry(self, operation):
        last_error: sqlite3.Error | None = None
        for attempt in range(4):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                last_error = exc
                text = str(exc).casefold()
                if "locked" not in text and "busy" not in text:
                    raise
                if attempt == 3:
                    break
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise RuntimeError("SQLite operation did not run")

    def _execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self._run_with_retry(lambda: self.conn.execute(statement, parameters))

    def _commit(self) -> None:
        self._run_with_retry(self.conn.commit)

    def ensure_column(self, table: str, column: str, column_type: str) -> None:
        rows = self._execute(f"PRAGMA table_info({table})").fetchall()
        if column not in {row["name"] for row in rows}:
            self._execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    @staticmethod
    def now_text() -> str:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    def start_scan(self, mode: str, roots: list[str]) -> int:
        now = self.now_text()
        cur = self._execute(
            """
            INSERT INTO scans(mode, root_paths, started_at, status)
            VALUES (?, ?, ?, ?)
            """,
            (mode, json.dumps(roots), now, "running"),
        )
        self._commit()
        return int(cur.lastrowid)

    def finish_scan(self, scan_id: int, stats: dict[str, Any], status: str = "complete") -> None:
        self._execute(
            """
            UPDATE scans
            SET completed_at = ?,
                status = ?,
                files_scanned = ?,
                bytes_scanned = ?,
                duplicate_groups = ?,
                duplicate_review_bytes = ?,
                duplicate_involved_bytes = ?,
                biggest_duplicate_group_bytes = ?,
                errors = ?,
                files_discovered = ?,
                files_skipped = ?,
                files_hashed = ?,
                partial_hash_candidates = ?,
                full_hash_candidates = ?,
                cache_hits = ?,
                last_error_path = ?,
                last_error_phase = ?,
                last_error_message = ?,
                largest_skipped_size = ?,
                duration_seconds = ?,
                avg_files_per_second = ?,
                avg_megabytes_per_second = ?
            WHERE id = ?
            """,
            (
                self.now_text(),
                status,
                int(stats.get("files_scanned", 0)),
                int(stats.get("bytes_scanned", 0)),
                int(stats.get("duplicate_groups", 0)),
                int(stats.get("duplicate_review_bytes", 0)),
                int(stats.get("duplicate_involved_bytes", 0)),
                int(stats.get("biggest_duplicate_group_bytes", 0)),
                int(stats.get("errors", 0)),
                int(stats.get("files_discovered", 0)),
                int(stats.get("files_skipped", 0)),
                int(stats.get("files_hashed", 0)),
                int(stats.get("partial_hash_candidates", 0)),
                int(stats.get("full_hash_candidates", 0)),
                int(stats.get("cache_hits", 0)),
                str(stats.get("last_error_path", "")),
                str(stats.get("last_error_phase", "")),
                str(stats.get("last_error_message", ""))[:1000],
                int(stats.get("largest_skipped_size", 0)),
                float(stats.get("duration_seconds", 0)),
                float(stats.get("avg_files_per_second", 0)),
                float(stats.get("avg_megabytes_per_second", 0)),
                scan_id,
            ),
        )
        self._commit()

    def cached_hashes(self, path: str, size: int, mtime_ns: int) -> tuple[str | None, str | None]:
        row = self._execute(
            """
            SELECT partial_hash, full_hash
            FROM files
            WHERE path = ? AND size = ? AND mtime_ns = ?
            """,
            (path, size, mtime_ns),
        ).fetchone()
        if row is None:
            return None, None
        return row["partial_hash"], row["full_hash"]

    def upsert_file(self, scan_id: int, record: FileRecord) -> None:
        now = self.now_text()
        self._execute(
            """
            INSERT INTO files(
                path, folder, drive, filename, extension, size, mtime_ns, modified_at,
                partial_hash, full_hash, hardlink_key, hardlink_count,
                last_seen_scan_id, scan_date, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                folder = excluded.folder,
                drive = excluded.drive,
                filename = excluded.filename,
                extension = excluded.extension,
                size = excluded.size,
                mtime_ns = excluded.mtime_ns,
                modified_at = excluded.modified_at,
                partial_hash = excluded.partial_hash,
                full_hash = excluded.full_hash,
                hardlink_key = excluded.hardlink_key,
                hardlink_count = excluded.hardlink_count,
                last_seen_scan_id = excluded.last_seen_scan_id,
                scan_date = excluded.scan_date,
                updated_at = excluded.updated_at
            """,
            (
                record.path,
                record.folder,
                record.drive,
                record.filename,
                record.extension,
                int(record.size),
                int(record.mtime_ns),
                record.modified_at,
                record.partial_hash,
                record.full_hash,
                record.hardlink_key,
                int(record.hardlink_count),
                scan_id,
                now,
                now,
            ),
        )

    def commit(self) -> None:
        self._commit()

    def update_hashes(
        self,
        path: str,
        partial_hash: str | None = None,
        full_hash: str | None = None,
    ) -> None:
        self._execute(
            """
            UPDATE files
            SET partial_hash = COALESCE(?, partial_hash),
                full_hash = COALESCE(?, full_hash),
                updated_at = ?
            WHERE path = ?
            """,
            (partial_hash, full_hash, self.now_text(), path),
        )

    def add_error(
        self,
        scan_id: int,
        path: str,
        error_type: str,
        message: str,
        phase: str = "",
    ) -> None:
        self._execute(
            """
            INSERT INTO scan_errors(scan_id, path, phase, error_type, error_message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (scan_id, path, phase, error_type, message[:1000], self.now_text()),
        )

    def candidate_sizes(self, scan_id: int) -> list[int]:
        rows = self.conn.execute(
            """
            SELECT size
            FROM files
            WHERE last_seen_scan_id = ?
            GROUP BY size
            HAVING COUNT(*) > 1
            ORDER BY size DESC
            """,
            (scan_id,),
        ).fetchall()
        return [int(row["size"]) for row in rows]

    def candidate_size_count(self, scan_id: int) -> int:
        row = self._execute(
            """
            SELECT COUNT(*) AS count
            FROM (
                SELECT size
                FROM files
                WHERE last_seen_scan_id = ?
                GROUP BY size
                HAVING COUNT(*) > 1
            )
            """,
            (scan_id,),
        ).fetchone()
        return int(row["count"] if row else 0)

    def iter_candidate_sizes(self, scan_id: int, batch_size: int = 100):
        last_size: int | None = None
        while True:
            if last_size is None:
                rows = self._execute(
                    """
                    SELECT size
                    FROM files
                    WHERE last_seen_scan_id = ?
                    GROUP BY size
                    HAVING COUNT(*) > 1
                    ORDER BY size DESC
                    LIMIT ?
                    """,
                    (scan_id, int(batch_size)),
                ).fetchall()
            else:
                rows = self._execute(
                    """
                    SELECT size
                    FROM files
                    WHERE last_seen_scan_id = ? AND size < ?
                    GROUP BY size
                    HAVING COUNT(*) > 1
                    ORDER BY size DESC
                    LIMIT ?
                    """,
                    (scan_id, last_size, int(batch_size)),
                ).fetchall()
            if not rows:
                return
            for row in rows:
                last_size = int(row["size"])
                yield last_size

    def candidate_file_count(self, scan_id: int) -> int:
        row = self._execute(
            """
            SELECT COUNT(*) AS count
            FROM files f
            JOIN (
                SELECT size
                FROM files
                WHERE last_seen_scan_id = ?
                GROUP BY size
                HAVING COUNT(*) > 1
            ) candidates ON candidates.size = f.size
            WHERE f.last_seen_scan_id = ?
            """,
            (scan_id, scan_id),
        ).fetchone()
        return int(row["count"] if row else 0)

    def iter_files_for_size(self, scan_id: int, size: int, batch_size: int = 500):
        yield from self._iter_file_records(
            "last_seen_scan_id = ? AND size = ?",
            (scan_id, int(size)),
            batch_size,
        )

    def iter_candidate_partial_hashes(self, scan_id: int, size: int, batch_size: int = 100):
        last_hash = ""
        while True:
            rows = self._execute(
                """
                SELECT partial_hash
                FROM files
                WHERE last_seen_scan_id = ?
                  AND size = ?
                  AND partial_hash IS NOT NULL
                  AND partial_hash > ?
                GROUP BY partial_hash
                HAVING COUNT(*) > 1
                ORDER BY partial_hash
                LIMIT ?
                """,
                (scan_id, int(size), last_hash, int(batch_size)),
            ).fetchall()
            if not rows:
                return
            for row in rows:
                last_hash = str(row["partial_hash"])
                yield last_hash

    def partial_hash_candidate_file_count(self, scan_id: int) -> int:
        row = self._execute(
            """
            SELECT COUNT(*) AS count
            FROM files f
            JOIN (
                SELECT size, partial_hash
                FROM files
                WHERE last_seen_scan_id = ? AND partial_hash IS NOT NULL
                GROUP BY size, partial_hash
                HAVING COUNT(*) > 1
            ) candidates
              ON candidates.size = f.size AND candidates.partial_hash = f.partial_hash
            WHERE f.last_seen_scan_id = ?
            """,
            (scan_id, scan_id),
        ).fetchone()
        return int(row["count"] if row else 0)

    def iter_files_for_partial_hash(
        self,
        scan_id: int,
        size: int,
        partial_hash: str,
        batch_size: int = 500,
    ):
        yield from self._iter_file_records(
            "last_seen_scan_id = ? AND size = ? AND partial_hash = ?",
            (scan_id, int(size), partial_hash),
            batch_size,
        )

    def _iter_file_records(self, where: str, params: tuple[Any, ...], batch_size: int):
        last_path = ""
        while True:
            rows = self._execute(
                f"""
                SELECT path, folder, drive, filename, extension, size, mtime_ns, modified_at,
                       partial_hash, full_hash, hardlink_key, hardlink_count
                FROM files
                WHERE {where} AND path > ?
                ORDER BY path
                LIMIT ?
                """,
                (*params, last_path, int(batch_size)),
            ).fetchall()
            if not rows:
                return
            for row in rows:
                last_path = str(row["path"])
                yield FileRecord(
                    path=row["path"],
                    folder=row["folder"],
                    drive=row["drive"],
                    filename=row["filename"],
                    extension=row["extension"],
                    size=int(row["size"]),
                    mtime_ns=int(row["mtime_ns"]),
                    modified_at=row["modified_at"],
                    partial_hash=row["partial_hash"],
                    full_hash=row["full_hash"],
                    hardlink_key=row["hardlink_key"],
                    hardlink_count=int(row["hardlink_count"] or 1),
                )

    def files_for_size(self, scan_id: int, size: int) -> list[FileRecord]:
        rows = self.conn.execute(
            """
            SELECT path, folder, drive, filename, extension, size, mtime_ns, modified_at,
                   partial_hash, full_hash, hardlink_key, hardlink_count
            FROM files
            WHERE last_seen_scan_id = ? AND size = ?
            ORDER BY path
            """,
            (scan_id, size),
        ).fetchall()
        return [
            FileRecord(
                path=row["path"],
                folder=row["folder"],
                drive=row["drive"],
                filename=row["filename"],
                extension=row["extension"],
                size=int(row["size"]),
                mtime_ns=int(row["mtime_ns"]),
                modified_at=row["modified_at"],
                partial_hash=row["partial_hash"],
                full_hash=row["full_hash"],
                hardlink_key=row["hardlink_key"],
                hardlink_count=int(row["hardlink_count"] or 1),
            )
            for row in rows
        ]

    def duplicate_stats(self, scan_id: int) -> dict[str, int]:
        row = self._execute(
            """
            SELECT COUNT(*) AS duplicate_groups,
                   COALESCE(SUM((file_count - 1) * size), 0) AS duplicate_review_bytes,
                   COALESCE(SUM(file_count * size), 0) AS duplicate_involved_bytes,
                   COALESCE(MAX(file_count * size), 0) AS biggest_duplicate_group_bytes
            FROM (
                SELECT size, COUNT(*) AS file_count
                FROM files
                WHERE last_seen_scan_id = ?
                  AND full_hash IS NOT NULL
                GROUP BY full_hash, size
                HAVING COUNT(*) > 1
            )
            """,
            (scan_id,),
        ).fetchone()
        return {
            "duplicate_groups": int(row["duplicate_groups"] if row else 0),
            "duplicate_review_bytes": int(row["duplicate_review_bytes"] if row else 0),
            "duplicate_involved_bytes": int(row["duplicate_involved_bytes"] if row else 0),
            "biggest_duplicate_group_bytes": int(row["biggest_duplicate_group_bytes"] if row else 0),
        }

    def latest_scan_id(self) -> int | None:
        row = self._execute(
            """
            SELECT id
            FROM scans
            WHERE status IN ('complete', 'complete_with_errors', 'stopped_by_user', 'failed')
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        return int(row["id"]) if row else None

    def scan_row(self, scan_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        return dict(row) if row else None

    def scans(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM scans ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]

    def file_rows(self, scan_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM files
            WHERE last_seen_scan_id = ?
            ORDER BY size DESC, path
            """,
            (scan_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def iter_file_rows(self, scan_id: int, batch_size: int = 500):
        last_path = ""
        while True:
            rows = self._execute(
                """
                SELECT *
                FROM files
                WHERE last_seen_scan_id = ? AND path > ?
                ORDER BY path
                LIMIT ?
                """,
                (scan_id, last_path, int(batch_size)),
            ).fetchall()
            if not rows:
                return
            for row in rows:
                last_path = str(row["path"])
                yield dict(row)

    def iter_query_rows(self, statement: str, parameters: tuple[Any, ...], batch_size: int = 500):
        cursor = self._execute(statement, parameters)
        while True:
            rows = cursor.fetchmany(int(batch_size))
            if not rows:
                return
            for row in rows:
                yield dict(row)

    def file_rows_filtered(
        self,
        scan_id: int,
        filters: dict[str, Any] | None = None,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "size DESC, path",
    ) -> list[dict[str, Any]]:
        where, params = self._file_where(scan_id, filters or {})
        safe_order = {
            "size DESC, path",
            "path",
            "modified_at DESC",
            "age",
        }
        if order_by not in safe_order:
            order_by = "size DESC, path"
        if order_by == "age":
            order_by = "modified_at ASC, path"
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM files
            WHERE {where}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            (*params, int(limit), int(offset)),
        ).fetchall()
        return [dict(row) for row in rows]

    def file_rows_count(self, scan_id: int, filters: dict[str, Any] | None = None) -> int:
        where, params = self._file_where(scan_id, filters or {})
        row = self.conn.execute(
            f"SELECT COUNT(*) AS count FROM files WHERE {where}",
            params,
        ).fetchone()
        return int(row["count"] if row else 0)

    def _file_where(self, scan_id: int, filters: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
        clauses = ["last_seen_scan_id = ?"]
        params: list[Any] = [scan_id]
        drives = filters.get("drives") or []
        if drives:
            clauses.append(f"drive IN ({','.join('?' for _ in drives)})")
            params.extend(drives)
        extensions = filters.get("extensions") or []
        if extensions:
            clauses.append(f"extension IN ({','.join('?' for _ in extensions)})")
            params.extend(extensions)
        folder_query = str(filters.get("folder_query") or "").strip()
        if folder_query:
            clauses.append("folder LIKE ?")
            params.append(f"%{folder_query}%")
        search = str(filters.get("search") or "").strip()
        if search:
            clauses.append("(filename LIKE ? OR path LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        min_size = filters.get("min_size")
        if min_size is not None:
            clauses.append("size >= ?")
            params.append(int(min_size))
        extension_family = filters.get("extension_family")
        if extension_family:
            clauses.append(f"extension IN ({','.join('?' for _ in extension_family)})")
            params.extend(extension_family)
        return " AND ".join(clauses), tuple(params)

    def related_rows_for_labels(
        self,
        scan_id: int,
        rows: list[dict[str, Any]],
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        hashes = sorted({row["full_hash"] for row in rows if row.get("full_hash")})
        names = sorted({row["filename"] for row in rows if row.get("filename")})
        sizes = sorted({int(row["size"]) for row in rows if row.get("size") is not None})
        clauses = ["path IN ({})".format(",".join("?" for _ in rows))]
        params: list[Any] = [row["path"] for row in rows]
        if hashes:
            clauses.append(f"full_hash IN ({','.join('?' for _ in hashes)})")
            params.extend(hashes)
        if names:
            clauses.append(f"filename IN ({','.join('?' for _ in names)})")
            params.extend(names)
        if sizes:
            clauses.append(f"size IN ({','.join('?' for _ in sizes)})")
            params.extend(sizes)
        if not rows:
            return []
        query = f"""
            SELECT *
            FROM files
            WHERE last_seen_scan_id = ?
              AND ({' OR '.join(clauses)})
            LIMIT ?
        """
        out = self._execute(query, (scan_id, *params, int(limit))).fetchall()
        return [dict(row) for row in out]

    def distinct_values(self, scan_id: int, column: str) -> list[str]:
        if column not in {"drive", "extension"}:
            raise ValueError("Unsupported distinct column")
        rows = self.conn.execute(
            f"""
            SELECT DISTINCT {column} AS value
            FROM files
            WHERE last_seen_scan_id = ?
            ORDER BY value
            """,
            (scan_id,),
        ).fetchall()
        return [str(row["value"] or "") for row in rows]

    def drive_summary_rows(self, scan_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT drive, COUNT(*) AS file_count, COALESCE(SUM(size), 0) AS total_bytes
            FROM files
            WHERE last_seen_scan_id = ?
            GROUP BY drive
            ORDER BY total_bytes DESC
            """,
            (scan_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def extension_summary_rows(self, scan_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT extension, COUNT(*) AS file_count, COALESCE(SUM(size), 0) AS total_bytes
            FROM files
            WHERE last_seen_scan_id = ?
            GROUP BY extension
            ORDER BY total_bytes DESC
            LIMIT 500
            """,
            (scan_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def folder_summary_rows(self, scan_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT folder, COUNT(*) AS file_count, COALESCE(SUM(size), 0) AS total_bytes
            FROM files
            WHERE last_seen_scan_id = ?
            GROUP BY folder
            ORDER BY total_bytes DESC
            LIMIT ?
            """,
            (scan_id, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def duplicate_group_rows(self, scan_id: int, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT full_hash,
                   MIN(filename) AS sample_filename,
                   MIN(size) AS file_size_bytes,
                   COUNT(*) AS file_count,
                   (COUNT(*) - 1) * MIN(size) AS wasted_bytes,
                   COUNT(*) * MIN(size) AS involved_bytes,
                   GROUP_CONCAT(DISTINCT drive) AS drives
            FROM files
            WHERE last_seen_scan_id = ?
              AND full_hash IS NOT NULL
            GROUP BY full_hash, size
            HAVING COUNT(*) > 1
            ORDER BY wasted_bytes DESC
            LIMIT ?
            """,
            (scan_id, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def duplicate_file_rows(self, scan_id: int, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT f.*
            FROM files f
            JOIN (
                SELECT full_hash, size
                FROM files
                WHERE last_seen_scan_id = ?
                  AND full_hash IS NOT NULL
                GROUP BY full_hash, size
                HAVING COUNT(*) > 1
            ) d ON f.full_hash = d.full_hash AND f.size = d.size
            WHERE f.last_seen_scan_id = ?
            ORDER BY f.size DESC, f.path
            LIMIT ?
            """,
            (scan_id, scan_id, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def p_files_on_b_rows(self, scan_id: int, portable_drive: str, backup_drive: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT p.*
            FROM files p
            WHERE p.last_seen_scan_id = ?
              AND p.drive = ?
              AND p.full_hash IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM files b
                  WHERE b.last_seen_scan_id = p.last_seen_scan_id
                    AND b.drive = ?
                    AND b.full_hash = p.full_hash
              )
            ORDER BY p.size DESC, p.path
            LIMIT ?
            """,
            (scan_id, portable_drive, backup_drive, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def p_files_not_on_b_rows(self, scan_id: int, portable_drive: str, backup_drive: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT p.*
            FROM files p
            WHERE p.last_seen_scan_id = ?
              AND p.drive = ?
              AND (
                  p.full_hash IS NULL OR NOT EXISTS (
                      SELECT 1 FROM files b
                      WHERE b.last_seen_scan_id = p.last_seen_scan_id
                        AND b.drive = ?
                        AND b.full_hash = p.full_hash
                  )
              )
            ORDER BY p.size DESC, p.path
            LIMIT ?
            """,
            (scan_id, portable_drive, backup_drive, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def old_editing_rows(self, scan_id: int, portable_drive: str, extensions: list[str], limit: int = 200) -> list[dict[str, Any]]:
        if not extensions:
            return []
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM files
            WHERE last_seen_scan_id = ?
              AND drive = ?
              AND extension IN ({','.join('?' for _ in extensions)})
            ORDER BY modified_at ASC, size DESC
            LIMIT ?
            """,
            (scan_id, portable_drive, *extensions, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def error_rows(self, scan_id: int, limit: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM scan_errors WHERE scan_id = ? ORDER BY id DESC"
        params: tuple[Any, ...] = (scan_id,)
        if limit is not None:
            query += " LIMIT ?"
            params = (scan_id, int(limit))
        rows = self._execute(query, params).fetchall()
        rows = list(reversed(rows))
        return [dict(row) for row in rows]

    def error_count(self, scan_id: int) -> int:
        row = self._execute(
            "SELECT COUNT(*) AS count FROM scan_errors WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        return int(row["count"] if row else 0)

    def latest_error(self, scan_id: int) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM scan_errors WHERE scan_id = ? ORDER BY id DESC LIMIT 1",
            (scan_id,),
        ).fetchone()
        return dict(row) if row else None

    def iter_error_rows(self, scan_id: int, batch_size: int = 500):
        last_id = 0
        while True:
            rows = self._execute(
                """
                SELECT *
                FROM scan_errors
                WHERE scan_id = ? AND id > ?
                ORDER BY id
                LIMIT ?
                """,
                (scan_id, last_id, int(batch_size)),
            ).fetchall()
            if not rows:
                return
            for row in rows:
                last_id = int(row["id"])
                yield dict(row)

    def database_info(self) -> dict[str, Any]:
        journal_mode = self._execute("PRAGMA journal_mode").fetchone()
        return {
            "path": str(self.db_path),
            "size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "journal_mode": str(journal_mode[0] if journal_mode else "unknown"),
        }

    def optimize(self) -> None:
        self._execute("PRAGMA optimize")
