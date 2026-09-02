import json
from pathlib import Path
from typing import Any

from .paths import PROJECT_ROOT


DEFAULT_CONFIG: dict[str, Any] = {
    "backup_drive": "B",
    "portable_drive": "P",
    "database_path": "{project_root}\\data\\storage_cache.sqlite3",
    "progress_path": "{project_root}\\data\\scan_progress.json",
    "scan_control_path": "{project_root}\\data\\scan_control.json",
    "stop_request_path": "{project_root}\\data\\stop_scan.json",
    "report_dir": "{project_root}\\reports",
    "include_app_generated_files": False,
    "test_paths": ["{project_root}\\test_data"],
    "test_drive_aliases": {
        "{project_root}\\test_data\\B_reference": "B",
        "{project_root}\\test_data\\P_portable": "P",
        "{project_root}\\test_data\\C_local": "TEST",
    },
    "drive_allowlist": [],
    "include_network_drives": False,
    "include_system_folders": False,
    "scan_recycle_bin": False,
    "follow_symlinks": False,
    "partial_hash_bytes": 4 * 1024 * 1024,
    "hash_progress_bytes": 16 * 1024 * 1024,
    "hash_commit_every_files": 500,
    "hash_algorithm": "sha256",
    "full_hash_max_mb": 0,
    "full_hash_large_files": True,
    "skip_files_above_mb": 0,
    "progress_stall_seconds": 45,
    "heartbeat_seconds": 5,
    "large_file_threshold_mb": 500,
    "old_file_days": 365,
    "report_max_rows_per_sheet": 200000,
    "commit_every_files": 500,
    "media_only_extensions": [
        ".jpg",
        ".jpeg",
        ".png",
        ".heic",
        ".dng",
        ".nef",
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".prproj",
        ".drp",
        ".psd",
        ".psb",
        ".wav",
    ],
    "possible_old_editing_extensions": [
        ".aep",
        ".aepx",
        ".braw",
        ".c4d",
        ".dng",
        ".drp",
        ".fcpxml",
        ".mov",
        ".mp4",
        ".mxf",
        ".prproj",
        ".psb",
        ".psd",
        ".r3d",
        ".wav",
    ],
    "excluded_folder_names": [
        "System Volume Information",
        "$Recycle.Bin",
        "pagefile.sys",
        "hiberfil.sys",
        "swapfile.sys",
        ".git",
        "Recovery",
        "Windows",
        "Program Files",
        "Program Files (x86)",
        "ProgramData",
        "PerfLogs",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        "cache",
        "caches",
        "temp",
        "tmp",
        "Adobe Media Cache",
        "Media Cache Files",
        "Peak Files",
        "Video Previews",
        "Audio Previews",
        "Encoded Files",
        "Conformed Audio Files",
        "Lightroom Catalog Previews.lrdata",
        "Lightroom Catalog Smart Previews.lrdata",
        "Previews.lrdata",
        "Smart Previews.lrdata",
        ".streamlit",
        "reports",
        "data",
        "ThumbCache",
        "Explorer",
    ],
    "excluded_path_fragments": [
        "{project_root}\\data\\",
        "{project_root}\\reports\\",
        "{project_root}\\.git\\",
        "{project_root}\\.venv\\",
        "{project_root}\\.streamlit\\",
        "\\AppData\\Local\\Temp\\",
        "\\AppData\\Local\\Microsoft\\Windows\\INetCache\\",
        "\\AppData\\Local\\Microsoft\\Windows\\WebCache\\",
        "\\AppData\\Local\\Packages\\",
        "\\AppData\\Local\\CrashDumps\\",
        "\\AppData\\Local\\Adobe\\Common\\Media Cache\\",
        "\\AppData\\Roaming\\Adobe\\Common\\Media Cache\\",
        "\\AppData\\Local\\Microsoft\\Windows\\Explorer\\",
        "\\AppData\\Local\\Microsoft\\Windows\\Caches\\",
    ],
    "excluded_path_patterns": [],
}


def _expand_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("{project_root}", str(PROJECT_ROOT))
    if isinstance(value, list):
        return [_expand_value(item) for item in value]
    if isinstance(value, dict):
        return {_expand_value(key): _expand_value(item) for key, item in value.items()}
    return value


def _merge_config(user_config: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_CONFIG.copy()
    merged.update(user_config)
    return merged


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    candidate = Path(config_path) if config_path else PROJECT_ROOT / "config.json"
    if candidate.exists():
        raw = json.loads(candidate.read_text(encoding="utf-8"))
        config = _merge_config(raw)
    else:
        example = PROJECT_ROOT / "config.example.json"
        if example.exists():
            raw = json.loads(example.read_text(encoding="utf-8"))
            config = _merge_config(raw)
        else:
            config = DEFAULT_CONFIG.copy()

    expanded = _expand_value(config)
    for key in ("database_path", "progress_path", "scan_control_path", "stop_request_path", "report_dir"):
        expanded[key] = str(Path(expanded[key]))
    expanded["backup_drive"] = str(expanded["backup_drive"]).rstrip(":").upper()
    expanded["portable_drive"] = str(expanded["portable_drive"]).rstrip(":").upper()
    expanded["possible_old_editing_extensions"] = [
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in expanded.get("possible_old_editing_extensions", [])
    ]
    expanded["media_only_extensions"] = [
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in expanded.get("media_only_extensions", [])
    ]
    expanded["excluded_folder_names"] = [
        str(name).casefold() for name in expanded.get("excluded_folder_names", [])
    ]
    if expanded.get("include_app_generated_files", False):
        app_root = str(PROJECT_ROOT).casefold()
        expanded["excluded_path_fragments"] = [
            str(fragment).casefold()
            for fragment in expanded.get("excluded_path_fragments", [])
            if not str(fragment).casefold().startswith(app_root)
        ]
    else:
        expanded["excluded_path_fragments"] = [
            str(fragment).casefold() for fragment in expanded.get("excluded_path_fragments", [])
        ]
    return expanded


def test_drive_aliases(config: dict[str, Any]) -> list[tuple[str, str]]:
    aliases = []
    for raw_path, raw_drive in config.get("test_drive_aliases", {}).items():
        root = str(Path(raw_path)).rstrip("\\/")
        drive = str(raw_drive).rstrip(":").upper()
        aliases.append((root.casefold(), drive))
    aliases.sort(key=lambda item: len(item[0]), reverse=True)
    return aliases


def drive_for_path(path: str | Path, config: dict[str, Any]) -> str:
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    folded = text.casefold()
    for alias_root, drive in test_drive_aliases(config):
        if folded == alias_root or folded.startswith(alias_root + "\\"):
            return drive
    drive = Path(text).drive
    if drive:
        return drive.rstrip(":").upper()
    return "UNKNOWN"
