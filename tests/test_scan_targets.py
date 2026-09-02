import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from storage_optimiser import cli
from storage_optimiser.scan_control import start_background_scan
from storage_optimiser.scanner import StorageScanner


class ScanTargetTests(unittest.TestCase):
    def test_background_scan_passes_each_selected_root_to_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "scan_control_path": str(Path(temp_dir) / "scan_control.json"),
                "stop_request_path": str(Path(temp_dir) / "stop_scan.json"),
                "progress_path": str(Path(temp_dir) / "scan_progress.json"),
            }
            process = Mock(pid=1234)

            with patch("storage_optimiser.scan_control.ensure_app_dirs"), patch(
                "storage_optimiser.scan_control.scan_status", return_value={"status": "idle"}
            ), patch("storage_optimiser.scan_control.subprocess.Popen", return_value=process) as popen:
                start_background_scan("media_only", ["B:\\", "P:\\"], config)

            command = popen.call_args.args[0]
            self.assertEqual(command[-4:], ["--root", "B:\\", "--root", "P:\\"])

    def test_explicit_roots_override_mode_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_one = Path(temp_dir) / "one"
            root_two = Path(temp_dir) / "two"
            root_one.mkdir()
            root_two.mkdir()
            cache = Mock()
            cache.start_scan.return_value = 7
            cache.duplicate_stats.return_value = {
                "duplicate_groups": 0,
                "duplicate_review_bytes": 0,
                "duplicate_involved_bytes": 0,
                "biggest_duplicate_group_bytes": 0,
            }
            scanner = StorageScanner(
                {
                    "progress_path": str(Path(temp_dir) / "progress.json"),
                    "stop_request_path": str(Path(temp_dir) / "stop_scan.json"),
                },
                cache,
            )
            scanner.emit = Mock()
            scanner.walk_root = Mock()
            scanner.hash_candidates = Mock()
            scanner.estimate_roots_used = Mock(return_value=0)

            scanner.scan("full", roots=[str(root_one), str(root_two)])

            cache.start_scan.assert_called_once_with("full", [str(root_one), str(root_two)])
            self.assertEqual(
                [call.args[0] for call in scanner.walk_root.call_args_list],
                [str(root_one), str(root_two)],
            )

    def test_cli_passes_explicit_roots_to_scanner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "database_path": str(Path(temp_dir) / "cache.sqlite3"),
                "stop_request_path": str(Path(temp_dir) / "stop_scan.json"),
            }
            scanner = Mock()
            scanner.scan.return_value = {
                "files_scanned": 0,
                "bytes_scanned": 0,
                "duplicate_groups": 0,
                "duplicate_review_bytes": 0,
                "stopped": False,
            }
            with patch("storage_optimiser.cli.load_config", return_value=config), patch(
                "storage_optimiser.cli.StorageCache"
            ), patch("storage_optimiser.cli.StorageScanner", return_value=scanner), patch(
                "storage_optimiser.cli.mark_scan_done"
            ), patch(
                "sys.argv", ["cli.py", "scan", "--mode", "media_only", "--root", "B:\\", "--root", "P:\\"]
            ):
                cli.main()

            scanner.scan.assert_called_once_with("media_only", roots=["B:\\", "P:\\"])


if __name__ == "__main__":
    unittest.main()
