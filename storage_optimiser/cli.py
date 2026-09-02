import argparse
import sys
from pathlib import Path

from .cache import StorageCache
from .config import load_config
from .reports import export_csv_report, export_excel_report
from .scanner import StorageScanner, format_bytes
from .scan_control import mark_scan_done


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only storage optimisation tool")
    parser.add_argument("--config", default=None, help="Path to config JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="Run read-only scan")
    scan_parser.add_argument(
        "--mode",
        choices=["test", "backup_vs_portable", "media_only", "full"],
        default="test",
    )
    scan_parser.add_argument(
        "--root",
        dest="roots",
        action="append",
        default=None,
        help="Explicit scan root. Repeat for each selected root.",
    )
    scan_parser.add_argument("--report", action="store_true", help="Export Excel after scan")

    export_parser = sub.add_parser("export", help="Export Excel and CSV reports")
    export_parser.add_argument("--scan-id", type=int, default=None)
    export_parser.add_argument("--output", default=None)
    export_parser.add_argument("--format", choices=["excel", "csv", "both"], default="excel")
    export_parser.add_argument("--include-all-files", action="store_true", help="Include all_files.csv in CSV export")

    args = parser.parse_args()
    config = load_config(args.config)

    if args.command == "scan":
        stop_file = Path(config["stop_request_path"])
        if stop_file.exists():
            stop_file.unlink()
        cache = StorageCache(config["database_path"])
        try:
            scanner = StorageScanner(config, cache, progress_callback=print_progress)
            stats = scanner.scan(args.mode, roots=args.roots)
            print()
            print(f"Scan done: {stats['files_scanned']} files, {format_bytes(stats['bytes_scanned'])}.")
            print(f"Duplicate groups: {stats['duplicate_groups']}.")
            print(f"Space to review: {format_bytes(stats['duplicate_review_bytes'])}.")
            if stats.get("stopped"):
                phase = "stopped_by_user"
            elif int(stats.get("errors") or 0):
                phase = "complete_with_errors"
            else:
                phase = "complete"
            mark_scan_done(config, phase, f"Scan {phase}.")
        except Exception as exc:
            mark_scan_done(config, "failed", str(exc))
            raise
        finally:
            cache.close()
        if args.report:
            output = export_excel_report(args.config)
            print(f"Excel report: {output}")
        return

    if args.command == "export":
        if args.format in {"excel", "both"}:
            output = export_excel_report(args.config, args.output, args.scan_id)
            print(f"Excel report: {output}")
        if args.format in {"csv", "both"}:
            output = export_csv_report(args.config, None, args.scan_id, include_all_files=args.include_all_files)
            print(f"CSV report folder: {output}")


def print_progress(progress: dict) -> None:
    phase = progress.get("phase", "")
    files = progress.get("files_scanned", 0)
    size = format_bytes(progress.get("bytes_scanned", 0))
    groups = progress.get("duplicate_groups_found", progress.get("duplicate_groups", 0))
    current = progress.get("current_folder", "") or progress.get("current_drive", "")
    line = f"{phase}: {files} files, {size}, {groups} duplicate groups"
    if current:
        line += f" | {current}"
    safe_line = safe_console_text(line[:240])
    print(safe_line, end="\r", flush=True)


def safe_console_text(value: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return value.encode(encoding, errors="replace").decode(encoding, errors="replace")


if __name__ == "__main__":
    main()
