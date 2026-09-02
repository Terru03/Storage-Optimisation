import json
import os
import subprocess
import sys
import ctypes
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .paths import PROJECT_ROOT, ensure_app_dirs


def now_text() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def write_json(path: str | Path, payload: dict[str, Any], retries: int = 5) -> bool:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        for attempt in range(max(1, retries)):
            try:
                tmp.replace(target)
                return True
            except PermissionError:
                if attempt + 1 >= max(1, retries):
                    return False
                time.sleep(0.05 * (attempt + 1))
            except OSError:
                return False
        return False
    except (OSError, UnicodeError, TypeError):
        return False
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def is_pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def scan_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    control = read_json(config["scan_control_path"])
    progress = read_json(config["progress_path"])
    status = control.get("status") or progress.get("phase") or "idle"
    pid_running = is_pid_running(control.get("pid"))
    if status in {"running", "stopping", "stalled"} and not pid_running:
        terminal_phases = {"complete", "complete_with_errors", "stopped_by_user", "failed"}
        status = progress.get("phase") if progress.get("phase") in terminal_phases else "failed"
    heartbeat_at = progress.get("heartbeat_at") or progress.get("updated_at") or control.get("updated_at")
    heartbeat_age_seconds = None
    if heartbeat_at:
        try:
            heartbeat_age_seconds = max(
                0.0,
                (datetime.now(timezone.utc).astimezone() - datetime.fromisoformat(str(heartbeat_at))).total_seconds(),
            )
        except (TypeError, ValueError):
            heartbeat_age_seconds = None
    stall_seconds = max(10, int(config.get("progress_stall_seconds", 45)))
    message = control.get("message", "")
    if status == "running" and pid_running and heartbeat_age_seconds is not None and heartbeat_age_seconds > stall_seconds:
        status = "stalled"
        message = f"Scan may be stalled. No heartbeat for {int(heartbeat_age_seconds)} seconds."
    merged = {
        "status": status,
        "pid": control.get("pid"),
        "pid_running": pid_running,
        "scan_id": progress.get("scan_id"),
        "mode": progress.get("mode") or control.get("mode"),
        "phase": progress.get("phase", ""),
        "started_at": progress.get("started_at") or control.get("started_at"),
        "updated_at": progress.get("updated_at") or control.get("updated_at"),
        "heartbeat_at": heartbeat_at,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "current_drive": progress.get("current_drive", ""),
        "current_folder": progress.get("current_folder", ""),
        "current_file": progress.get("current_file", ""),
        "files_scanned": progress.get("files_scanned", 0),
        "files_discovered": progress.get("files_discovered", 0),
        "files_indexed": progress.get("files_indexed", 0),
        "files_hashed": progress.get("files_hashed", 0),
        "files_skipped": progress.get("files_skipped", 0),
        "errors": progress.get("errors", 0),
        "last_error_path": progress.get("last_error_path", ""),
        "last_error_phase": progress.get("last_error_phase", ""),
        "last_error_message": progress.get("last_error_message", ""),
        "partial_hash_candidates": progress.get("partial_hash_candidates", 0),
        "full_hash_candidates": progress.get("full_hash_candidates", 0),
        "cache_hits": progress.get("cache_hits", 0),
        "duration_seconds": progress.get("duration_seconds", 0),
        "avg_files_per_second": progress.get("avg_files_per_second", 0),
        "avg_megabytes_per_second": progress.get("avg_megabytes_per_second", 0),
        "bytes_scanned": progress.get("bytes_scanned", 0),
        "duplicate_groups_found": progress.get("duplicate_groups_found", 0),
        "estimated_space_involved": progress.get("estimated_space_involved", 0),
        "progress_ratio": progress.get("progress_ratio"),
        "message": message,
        "roots": control.get("roots", []),
    }
    return merged


def start_background_scan(mode: str, roots: list[str], config: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_app_dirs()
    config = config or load_config()
    existing = scan_status(config)
    if existing.get("status") in {"running", "stalled", "stopping"}:
        return existing
    stop_path = Path(config["stop_request_path"])
    if stop_path.exists():
        stop_path.unlink()
    command = [sys.executable, "-m", "storage_optimiser.cli", "scan", "--mode", mode]
    for root in roots:
        command.extend(["--root", root])
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    payload = {
        "status": "running",
        "pid": process.pid,
        "mode": mode,
        "roots": roots,
        "started_at": now_text(),
        "updated_at": now_text(),
        "message": "Scan running in background.",
    }
    write_json(config["scan_control_path"], payload)
    return payload


def request_stop(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    payload = {
        "requested_at": now_text(),
        "reason": "User requested stop from dashboard.",
    }
    write_json(config["stop_request_path"], payload)
    control = read_json(config["scan_control_path"])
    control.update(
        {
            "status": "stopping",
            "updated_at": now_text(),
            "message": "Stop requested. Scanner will stop after current file/hash operation.",
        }
    )
    write_json(config["scan_control_path"], control)
    return control


def mark_scan_done(config: dict[str, Any], status: str, message: str = "") -> None:
    control = read_json(config["scan_control_path"])
    control.update(
        {
            "status": status,
            "updated_at": now_text(),
            "message": message,
        }
    )
    write_json(config["scan_control_path"], control)
    stop_path = Path(config["stop_request_path"])
    if stop_path.exists() and status in {"complete", "complete_with_errors", "failed", "stopped_by_user"}:
        stop_path.unlink()


def stop_requested(config: dict[str, Any]) -> bool:
    return Path(config["stop_request_path"]).exists()
