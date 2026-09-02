import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storage_optimiser.cache import StorageCache
from storage_optimiser.reports import export_csv_report
from storage_optimiser.scan_control import read_json, scan_status, write_json
from storage_optimiser.scanner import StorageScanner, full_hash_file, partial_hash_file, path_for_io


def scanner_config(temp_dir: Path) -> dict:
    return {
        "progress_path": str(temp_dir / "scan_progress.json"),
        "stop_request_path": str(temp_dir / "stop_scan.json"),
        "test_paths": [str(temp_dir)],
        "partial_hash_bytes": 64,
        "commit_every_files": 1,
        "include_system_folders": True,
        "scan_recycle_bin": False,
        "follow_symlinks": False,
        "media_only_extensions": [".bin"],
    }


class ScanResilienceTests(unittest.TestCase):
    def test_progress_write_lock_keeps_previous_json_and_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scan_progress.json"
            path.write_text('{"phase": "old"}', encoding="utf-8")

            with patch("storage_optimiser.scan_control.Path.replace", side_effect=PermissionError("locked")):
                written = write_json(path, {"phase": "new"})

            self.assertFalse(written)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"phase": "old"}')

    def test_hash_error_logs_phase_and_scan_finishes_with_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bad_file = root / "bad.bin"
            good_file = root / "good.bin"
            bad_file.write_bytes(b"same content")
            good_file.write_bytes(b"same content")
            config = scanner_config(root)
            cache = StorageCache(root / "cache.sqlite3")
            try:
                scanner = StorageScanner(config, cache)

                def partial_hash_with_failure(path: str, size: int, chunk_size: int, algorithm: str = "sha256") -> str:
                    if path.endswith("bad.bin"):
                        raise PermissionError("locked for test")
                    return partial_hash_file(path, size, chunk_size, algorithm)

                with patch("storage_optimiser.scanner.partial_hash_file", side_effect=partial_hash_with_failure):
                    result = scanner.scan("full", roots=[str(root)])

                scan = cache.scan_row(result["scan_id"])
                errors = cache.error_rows(result["scan_id"])
                self.assertEqual(scan["status"], "complete_with_errors")
                self.assertEqual(errors[-1]["phase"], "partial_hash")
                self.assertEqual(errors[-1]["path"], str(bad_file))
            finally:
                cache.close()

    def test_file_disappearing_during_hash_is_logged_and_scan_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vanished_file = root / "vanished.bin"
            good_file = root / "good.bin"
            vanished_file.write_bytes(b"same content")
            good_file.write_bytes(b"same content")
            config = scanner_config(root)
            cache = StorageCache(root / "cache.sqlite3")
            try:
                scanner = StorageScanner(config, cache)

                def partial_hash_with_missing(path: str, size: int, chunk_size: int, algorithm: str = "sha256") -> str:
                    if path.endswith("vanished.bin"):
                        raise FileNotFoundError("file disappeared for test")
                    return partial_hash_file(path, size, chunk_size, algorithm)

                with patch("storage_optimiser.scanner.partial_hash_file", side_effect=partial_hash_with_missing):
                    result = scanner.scan("full", roots=[str(root)])

                errors = cache.error_rows(result["scan_id"])
                self.assertEqual(cache.scan_row(result["scan_id"])["status"], "complete_with_errors")
                self.assertEqual(errors[-1]["error_type"], "FileNotFoundError")
                self.assertEqual(errors[-1]["phase"], "partial_hash")
            finally:
                cache.close()

    def test_missing_root_finishes_with_errors_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = scanner_config(root)
            cache = StorageCache(root / "cache.sqlite3")
            try:
                result = StorageScanner(config, cache).scan("full", roots=[str(root / "missing")])
                scan = cache.scan_row(result["scan_id"])
                errors = cache.error_rows(result["scan_id"])
                self.assertEqual(scan["status"], "complete_with_errors")
                self.assertGreaterEqual(scan["files_skipped"], 1)
                self.assertGreater(scan["duration_seconds"], 0)
                self.assertEqual(errors[-1]["phase"], "discovering")
            finally:
                cache.close()

    def test_stop_request_marks_scan_stopped_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "one.bin").write_bytes(b"content")
            config = scanner_config(root)
            Path(config["stop_request_path"]).write_text("{}", encoding="utf-8")
            cache = StorageCache(root / "cache.sqlite3")
            try:
                result = StorageScanner(config, cache).scan("full", roots=[str(root)])
                scan = cache.scan_row(result["scan_id"])
                self.assertTrue(result["stopped"])
                self.assertEqual(scan["status"], "stopped_by_user")
            finally:
                cache.close()

    def test_corrupted_progress_json_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scan_progress.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(read_json(path), {})

    def test_stale_heartbeat_is_marked_stalled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control_path = root / "scan_control.json"
            progress_path = root / "scan_progress.json"
            write_json(control_path, {"status": "running", "pid": 1234})
            write_json(
                progress_path,
                {"phase": "scanning", "heartbeat_at": "2000-01-01T00:00:00+00:00"},
            )
            with patch("storage_optimiser.scan_control.is_pid_running", return_value=True):
                status = scan_status(
                    {
                        "scan_control_path": str(control_path),
                        "progress_path": str(progress_path),
                        "progress_stall_seconds": 10,
                    }
                )
            self.assertEqual(status["status"], "stalled")

    def test_sqlite_locked_operation_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = StorageCache(Path(temp_dir) / "cache.sqlite3")
            attempts = 0
            try:
                def flaky_operation() -> str:
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise sqlite3.OperationalError("database is locked")
                    return "done"

                with patch("storage_optimiser.cache.time.sleep"):
                    result = cache._run_with_retry(flaky_operation)
                self.assertEqual(result, "done")
                self.assertEqual(attempts, 2)
            finally:
                cache.close()

    def test_csv_export_streams_required_files_without_all_files_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_root = root / "data"
            data_root.mkdir()
            (data_root / "one.bin").write_bytes(b"same content")
            (data_root / "two.bin").write_bytes(b"same content")
            config = scanner_config(root)
            cache = StorageCache(root / "cache.sqlite3")
            try:
                result = StorageScanner(config, cache).scan("full", roots=[str(data_root)])
            finally:
                cache.close()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps({"database_path": str(root / "cache.sqlite3"), "report_dir": str(root / "reports")}),
                encoding="utf-8",
            )
            output = export_csv_report(config_path, scan_id=result["scan_id"])
            self.assertTrue((output / "summary.csv").exists())
            self.assertTrue((output / "duplicate_groups.csv").exists())
            self.assertTrue((output / "duplicate_files.csv").exists())
            self.assertTrue((output / "scan_errors.csv").exists())
            self.assertTrue((output / "folder_summary.csv").exists())
            self.assertFalse((output / "all_files.csv").exists())

    def test_unicode_and_empty_files_scan_with_long_path_support(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_root = root / "data"
            data_root.mkdir()
            (data_root / "ședință_测试.bin").write_bytes(b"")
            (data_root / "empty_copy.bin").write_bytes(b"")
            config = scanner_config(root)
            cache = StorageCache(root / "cache.sqlite3")
            try:
                result = StorageScanner(config, cache).scan("full", roots=[str(data_root)])
                self.assertEqual(cache.scan_row(result["scan_id"])["status"], "complete")
                self.assertEqual(cache.file_rows_count(result["scan_id"]), 2)
            finally:
                cache.close()
            long_path = "C:\\" + ("nested\\" * 45) + "file.bin"
            self.assertTrue(path_for_io(long_path).startswith("\\\\?\\"))

    def test_full_hash_checks_stop_between_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.bin"
            path.write_bytes(b"x" * 8192)
            checks = 0

            def check_stop() -> None:
                nonlocal checks
                checks += 1

            digest = full_hash_file(
                str(path),
                chunk_size=1024,
                stop_callback=check_stop,
                progress_bytes=1024,
            )
            self.assertTrue(digest)
            self.assertGreaterEqual(checks, 8)


if __name__ == "__main__":
    unittest.main()
