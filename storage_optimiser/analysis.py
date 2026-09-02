from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def human_bytes(num: int | float) -> str:
    value = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PB"


def path_uri(value: str | None) -> str:
    if not value:
        return ""
    try:
        return Path(value).resolve().as_uri()
    except ValueError:
        return ""


def enrich_files(rows: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    backup_drive = str(config.get("backup_drive", "B")).upper()
    portable_drive = str(config.get("portable_drive", "P")).upper()
    old_days = int(config.get("old_file_days", 365))
    large_bytes = int(float(config.get("large_file_threshold_mb", 500)) * 1024 * 1024)
    edit_exts = set(config.get("possible_old_editing_extensions", []))

    hash_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    size_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    name_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        size_groups[int(row.get("size") or 0)].append(row)
        name_groups[str(row.get("filename") or "").casefold()].append(row)
        if row.get("full_hash"):
            hash_groups[row["full_hash"]].append(row)

    duplicate_hashes = {key for key, members in hash_groups.items() if len(members) > 1}
    backup_hashes = {
        row["full_hash"]
        for row in rows
        if row.get("drive") == backup_drive and row.get("full_hash")
    }
    duplicate_hashes_with_b = {
        full_hash
        for full_hash, members in hash_groups.items()
        if len(members) > 1 and any(member.get("drive") == backup_drive for member in members)
    }
    duplicate_hashes_without_b = duplicate_hashes - duplicate_hashes_with_b

    same_size_different_content_sizes = {
        size
        for size, members in size_groups.items()
        if len(members) > 1 and content_signatures(members)
    }
    same_filename_different_content_names = {
        name
        for name, members in name_groups.items()
        if name and len(members) > 1 and content_signatures(members)
    }

    now = datetime.now(timezone.utc).astimezone()
    duplicate_review_bytes = 0
    duplicate_involved_bytes = 0
    biggest_group = 0
    group_ids: dict[str, int] = {}
    duplicate_order = sorted(
        duplicate_hashes,
        key=lambda item: int(hash_groups[item][0].get("size") or 0) * (len(hash_groups[item]) - 1),
        reverse=True,
    )
    for idx, full_hash in enumerate(duplicate_order, start=1):
        group_ids[full_hash] = idx
        members = hash_groups[full_hash]
        size = int(members[0].get("size") or 0)
        involved = size * len(members)
        duplicate_review_bytes += size * (len(members) - 1)
        duplicate_involved_bytes += involved
        biggest_group = max(biggest_group, involved)

    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        size = int(item.get("size") or 0)
        full_hash = item.get("full_hash")
        members = hash_groups.get(full_hash, []) if full_hash else []
        member_drives = sorted({member.get("drive", "") for member in members})
        modified_dt = parse_dt(str(item.get("modified_at") or ""))
        age_days = (now - modified_dt).days if modified_dt else 0
        exact_duplicate_exists_on_b = bool(full_hash and full_hash in backup_hashes and item.get("drive") != backup_drive)
        is_duplicate = bool(full_hash and full_hash in duplicate_hashes)
        duplicate_only_outside_b = bool(full_hash and full_hash in duplicate_hashes_without_b)
        same_filename_different_content = (
            str(item.get("filename") or "").casefold() in same_filename_different_content_names
        )
        same_size_different_content = size in same_size_different_content_sizes
        only_found_on_p = item.get("drive") == portable_drive and (
            not full_hash or not members or set(member_drives) == {portable_drive}
        )
        possible_old_editing_file = (
            item.get("drive") == portable_drive
            and str(item.get("extension") or "").lower() in edit_exts
            and age_days >= old_days
        )
        large_file = size >= large_bytes
        hardlinked = int(item.get("hardlink_count") or 1) > 1
        safe_to_review = exact_duplicate_exists_on_b or duplicate_only_outside_b or possible_old_editing_file or large_file
        labels = []
        if exact_duplicate_exists_on_b:
            labels.append("Exact duplicate exists on B: Yes")
        if duplicate_only_outside_b:
            labels.append("Duplicate only outside B: Yes")
        if same_filename_different_content:
            labels.append("Same filename but different content")
        if same_size_different_content:
            labels.append("Same size but different content")
        if only_found_on_p:
            labels.append("Only found on P: Yes")
        if possible_old_editing_file:
            labels.append("Possible old editing file")
        if large_file:
            labels.append("Large file")
        if hardlinked:
            labels.append("Hardlinked file")
        if safe_to_review:
            labels.append("Safe to review manually")

        item.update(
            {
                "group_id": group_ids.get(full_hash, ""),
                "size_human": human_bytes(size),
                "size_mb": round(size / 1024 / 1024, 3),
                "age_days": age_days,
                "duplicate_status": "Duplicate" if is_duplicate else "Unique or not matched",
                "exact_duplicate_exists_on_b": exact_duplicate_exists_on_b,
                "duplicate_exists_on_b": exact_duplicate_exists_on_b,
                "duplicate_only_outside_b": duplicate_only_outside_b,
                "same_filename_different_content": same_filename_different_content,
                "same_size_different_content": same_size_different_content,
                "only_found_on_p": only_found_on_p,
                "possible_old_editing_file": possible_old_editing_file,
                "large_file": large_file,
                "hardlinked_file": hardlinked,
                "safe_to_review_manually": safe_to_review,
                "labels": "; ".join(labels),
                "member_drives": ", ".join(member_drives),
                "folder_url": path_uri(item.get("folder")),
                "file_url": path_uri(item.get("path")),
            }
        )
        enriched.append(item)

    files_on_p_also_b = [
        item for item in enriched
        if item.get("drive") == portable_drive and item.get("duplicate_exists_on_b")
    ]
    files_already_backed_up_on_b = [
        item for item in enriched
        if item.get("drive") != backup_drive and item.get("duplicate_exists_on_b")
    ]
    metrics = {
        "total_files": len(enriched),
        "total_size": sum(int(row.get("size") or 0) for row in enriched),
        "duplicate_groups": len(duplicate_hashes),
        "duplicate_review_bytes": duplicate_review_bytes,
        "duplicate_involved_bytes": duplicate_involved_bytes,
        "biggest_duplicate_group_bytes": biggest_group,
        "files_already_backed_up_on_b": len(files_already_backed_up_on_b),
        "files_on_p_also_found_on_b": len(files_on_p_also_b),
        "backup_drive": backup_drive,
        "portable_drive": portable_drive,
        "large_file_threshold_bytes": large_bytes,
        "old_file_days": old_days,
    }
    return enriched, metrics


def content_signatures(members: list[dict[str, Any]]) -> bool:
    signatures = {
        (
            int(member.get("size") or 0),
            member.get("full_hash") or member.get("partial_hash") or "",
        )
        for member in members
    }
    return len(signatures) > 1


def duplicate_group_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        group_id = row.get("group_id")
        if group_id not in ("", None):
            groups[int(group_id)].append(row)
    result: list[dict[str, Any]] = []
    for group_id, members in groups.items():
        size = int(members[0].get("size") or 0)
        count = len(members)
        wasted = size * max(count - 1, 0)
        involved = size * count
        result.append(
            {
                "group_id": group_id,
                "file_count": count,
                "file_size_bytes": size,
                "file_size": human_bytes(size),
                "wasted_bytes": wasted,
                "wasted_space": human_bytes(wasted),
                "involved_bytes": involved,
                "involved_space": human_bytes(involved),
                "drives": ", ".join(sorted({str(member.get("drive") or "") for member in members})),
                "sample_filename": str(members[0].get("filename") or ""),
                "folders": " | ".join(sorted({str(member.get("folder") or "") for member in members})[:6]),
                "full_hash": str(members[0].get("full_hash") or ""),
                "safe_to_review_manually": any(bool(member.get("safe_to_review_manually")) for member in members),
            }
        )
    result.sort(key=lambda item: item["wasted_bytes"], reverse=True)
    return result


def google_takeout_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    takeout_exts = {".zip", ".json", ".jpg", ".jpeg", ".mp4", ".mov", ".heic", ".dng"}
    result = []
    for row in rows:
        path = str(row.get("path") or "").casefold()
        extension = str(row.get("extension") or "").casefold()
        if "takeout" in path or extension in takeout_exts:
            result.append(row)
    result.sort(key=lambda item: int(item.get("size") or 0), reverse=True)
    return result


def possible_duplicate_folders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    folder_rows = folder_summary(rows)
    buckets: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in folder_rows:
        buckets[(int(row["file_count"]), int(row["total_bytes"]))].append(row)
    result: list[dict[str, Any]] = []
    for (file_count, total_bytes), members in buckets.items():
        if len(members) < 2 or file_count == 0:
            continue
        for member in members:
            result.append(
                {
                    "signature": f"{file_count} files / {human_bytes(total_bytes)}",
                    "folder": member["folder"],
                    "open_folder": member.get("open_folder", ""),
                    "file_count": file_count,
                    "total_bytes": total_bytes,
                    "total_size": human_bytes(total_bytes),
                    "matching_folder_count": len(members),
                }
            )
    result.sort(key=lambda item: (item["total_bytes"], item["file_count"]), reverse=True)
    return result


def drive_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drives: dict[str, dict[str, Any]] = {}
    for row in rows:
        drive = str(row.get("drive") or "UNKNOWN")
        bucket = drives.setdefault(drive, {"drive": drive, "file_count": 0, "total_bytes": 0})
        bucket["file_count"] += 1
        bucket["total_bytes"] += int(row.get("size") or 0)
    result = list(drives.values())
    for row in result:
        row["total_size"] = human_bytes(row["total_bytes"])
    result.sort(key=lambda item: item["total_bytes"], reverse=True)
    return result


def folder_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    folders: dict[str, dict[str, Any]] = {}
    for row in rows:
        folder = str(row.get("folder") or str(Path(row.get("path", "")).parent))
        bucket = folders.setdefault(
            folder,
            {
                "folder": folder,
                "open_folder": path_uri(folder),
                "file_count": 0,
                "total_bytes": 0,
            },
        )
        bucket["file_count"] += 1
        bucket["total_bytes"] += int(row.get("size") or 0)
    result = list(folders.values())
    for row in result:
        row["total_size"] = human_bytes(row["total_bytes"])
    result.sort(key=lambda item: item["total_bytes"], reverse=True)
    return result


def extension_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    exts: dict[str, dict[str, Any]] = {}
    for row in rows:
        ext = str(row.get("extension") or "(none)").lower()
        bucket = exts.setdefault(ext, {"extension": ext, "file_count": 0, "total_bytes": 0})
        bucket["file_count"] += 1
        bucket["total_bytes"] += int(row.get("size") or 0)
    result = list(exts.values())
    for row in result:
        row["total_size"] = human_bytes(row["total_bytes"])
    result.sort(key=lambda item: item["total_bytes"], reverse=True)
    return result
