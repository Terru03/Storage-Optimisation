import html
import json
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from storage_optimiser import __version__
from storage_optimiser.analysis import enrich_files, human_bytes, path_uri
from storage_optimiser.cache import StorageCache
from storage_optimiser.config import load_config
from storage_optimiser.paths import PROJECT_ROOT
from storage_optimiser.reports import export_csv_report, export_excel_report, export_scan_errors_csv
from storage_optimiser.scan_control import request_stop, scan_status, start_background_scan
from storage_optimiser.scanner import StorageScanner, discover_windows_drive_details, discover_windows_drives


st.set_page_config(page_title="Storage Optimisation", layout="wide")

SCAN_MODES = {
    "test": {
        "label": "Test data only",
        "summary": "Scans only the project test_data folder with fake B:, P:, and TEST labels.",
        "risk": "Fast and safe for checks.",
    },
    "backup_vs_portable": {
        "label": "B: vs P:",
        "summary": "Scans only B: and P: to compare backup/reference files with portable edit files.",
        "risk": "Good first real scan if both drives are connected.",
    },
    "media_only": {
        "label": "Media only",
        "summary": "Scans selected drives but only configured photo, video, audio, and project files.",
        "risk": "Faster than full scan and aimed at editing storage.",
    },
    "full": {
        "label": "Full scan",
        "summary": "Scans selected fixed/removable drives, using exclusions from config.",
        "risk": "Can take hours. Needs confirmation.",
    },
}

FILE_COLUMNS = [
    "drive",
    "filename",
    "extension",
    "size_human",
    "age_days",
    "duplicate_status",
    "labels",
    "hardlinked_file",
    "folder_url",
    "file_url",
    "folder",
    "path",
]


def add_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg: #0b0f14;
            --sidebar: #0d131a;
            --panel: #111821;
            --card: #131c26;
            --card-alt: #162231;
            --field: #0f1620;
            --line: #263241;
            --line-soft: #1d2835;
            --accent: #6aa37c;
            --accent-blue: #5f9fb3;
            --accent-faint: rgba(106, 163, 124, .11);
            --text: #e8eef5;
            --text-soft: #b8c4d2;
            --muted: #8492a3;
            --muted-low: #5f6d7d;
            --warn: #f08873;
        }
        * {
            box-sizing: border-box;
        }
        .stApp {
            background: var(--bg);
            color: var(--text);
            font-family: "Segoe UI", "Aptos", "Helvetica Neue", sans-serif;
            line-height: 1.42;
        }
        h1, h2, h3, [data-testid="stMetricValue"] {
            font-family: "Segoe UI Semibold", "Segoe UI", sans-serif;
            letter-spacing: 0;
            color: var(--text);
            line-height: 1.18;
        }
        .block-container {
            padding-top: 1rem;
            padding-bottom: 2rem;
            max-width: 1720px;
        }
        section.main > div {
            min-width: 0;
        }
        .tech-header, .panel, .mode-card, .storage-card, .open-link {
            border: 1px solid var(--line);
            border-radius: 6px;
            background: var(--panel);
        }
        @keyframes fade-up {
            from { opacity: 0; transform: translateY(5px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @keyframes status-pulse {
            0%, 100% { opacity: .5; transform: scale(.84); }
            50% { opacity: 1; transform: scale(1); }
        }
        @keyframes tab-line {
            from { transform: scaleX(.35); transform-origin: left; }
            to { transform: scaleX(1); transform-origin: left; }
        }
        .tech-header {
            padding: 10px 12px;
            margin-bottom: 10px;
        }
        .tech-header-top {
            display: grid;
            grid-template-columns: minmax(190px, 270px) 1fr;
            align-items: center;
            gap: 12px;
        }
        .tech-header h1 {
            margin: 0;
            font-size: 26px;
            line-height: 1.12;
            word-break: break-word;
            overflow-wrap: anywhere;
        }
        .tech-header p {
            margin: 5px 0 0 0;
            color: var(--text-soft);
            font-size: 13px;
            line-height: 1.35;
        }
        .status-chip-row {
            display: grid;
            grid-template-columns: repeat(5, minmax(74px, 1fr));
            gap: 5px;
            min-width: 0;
        }
        .status-chip {
            border: 1px solid var(--line);
            background: var(--field);
            border-radius: 4px;
            padding: 5px 7px;
            min-width: 0;
        }
        .status-chip .chip-label {
            display: block;
            color: var(--muted);
            font-size: 9px;
            line-height: 1.2;
            text-transform: uppercase;
            font-weight: 700;
        }
        .status-chip .chip-value {
            display: block;
            color: var(--text);
            margin-top: 2px;
            font: 700 12px/1.15 "Cascadia Mono", "Consolas", monospace;
            overflow-wrap: anywhere;
        }
        @media (max-width: 980px) {
            .tech-header-top {
                grid-template-columns: 1fr;
            }
            .status-chip-row {
                grid-template-columns: repeat(2, minmax(96px, 1fr));
            }
        }
        .section-label {
            margin: 16px 0 8px 0;
            color: var(--text);
            font-size: 12px;
            font-weight: 800;
            line-height: 1.2;
            text-transform: uppercase;
            letter-spacing: .04em;
            border-bottom: 1px solid var(--line-soft);
            padding-bottom: 7px;
        }
        .panel {
            padding: 11px 13px;
            margin: 8px 0 12px 0;
            animation: fade-up 180ms ease both;
            transition: border-color 160ms ease, background 160ms ease, transform 160ms ease, box-shadow 160ms ease;
        }
        .mode-grid, .card-grid, .priority-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
            gap: 10px;
            margin: 8px 0 12px 0;
        }
        .mode-card {
            background: var(--card);
            padding: 11px 12px;
            min-height: 96px;
            animation: fade-up 180ms ease both;
            transition: border-color 160ms ease, background 160ms ease, transform 160ms ease, box-shadow 160ms ease;
        }
        .mode-card:hover, .storage-card:hover, .open-link:hover {
            border-color: rgba(106,163,124,.58);
            transform: translateY(-1px);
        }
        .mode-card strong {
            display: block;
            color: var(--text);
            font-size: 14px;
            margin-bottom: 5px;
            line-height: 1.25;
        }
        .mode-card span, .mode-card small {
            color: var(--text-soft);
            font-size: 12px;
            line-height: 1.35;
        }
        .mode-card small {
            display: block;
            margin-top: 6px;
            color: var(--muted);
        }
        .storage-card {
            background: var(--card);
            padding: 12px 13px;
            min-height: 90px;
            overflow: visible;
            animation: fade-up 180ms ease both;
            transition: border-color 160ms ease, background 160ms ease, transform 160ms ease, box-shadow 160ms ease;
        }
        .storage-card .label {
            font-size: 10px;
            line-height: 1.25;
            text-transform: uppercase;
            font-weight: 700;
            color: var(--muted);
            overflow-wrap: anywhere;
        }
        .storage-card .value {
            font-family: "Cascadia Mono", "Consolas", monospace;
            font-size: 22px;
            line-height: 1.15;
            margin-top: 7px;
            color: var(--text);
            overflow-wrap: anywhere;
        }
        .storage-card .sub {
            color: var(--text-soft);
            font-size: 12px;
            line-height: 1.35;
            margin-top: 6px;
            overflow-wrap: anywhere;
        }
        .pulse-box {
            border: 1px solid var(--line);
            border-left: 4px solid var(--accent);
            background: var(--panel);
            color: var(--text);
            padding: 11px 13px;
            border-radius: 6px;
            line-height: 1.42;
            font-size: 13px;
        }
        .status-dot {
            display: inline-block;
            width: 7px;
            height: 7px;
            margin-right: 5px;
            border-radius: 50%;
            vertical-align: 1px;
            background: var(--accent);
            animation: status-pulse 1.15s ease-in-out infinite;
        }
        .scan-target-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(172px, 1fr));
            gap: 8px;
            margin: 8px 0 12px 0;
        }
        .target-fixed-card {
            border: 1px solid var(--line);
            border-radius: 6px;
            background: var(--card);
            padding: 10px 11px;
            min-height: 68px;
            animation: fade-up 180ms ease both;
            transition: border-color 160ms ease, background 160ms ease, transform 160ms ease, box-shadow 160ms ease;
        }
        .target-fixed-card:hover {
            border-color: rgba(106,163,124,.58);
            transform: translateY(-1px);
        }
        .target-fixed-card strong {
            display: block;
            color: var(--text);
            font-family: "Cascadia Mono", "Consolas", monospace;
            font-size: 15px;
        }
        .target-fixed-card span {
            color: var(--text-soft);
            font-size: 12px;
            line-height: 1.3;
        }
        [class*="st-key-target-drive-card-"] {
            border-color: var(--line) !important;
            background: var(--card) !important;
            border-radius: 6px !important;
            padding: 10px 11px !important;
            animation: fade-up 180ms ease both;
            transition: border-color 160ms ease, background 160ms ease, transform 160ms ease, box-shadow 160ms ease;
        }
        [class*="st-key-target-drive-card-"]:hover {
            border-color: rgba(106,163,124,.58) !important;
            transform: translateY(-1px);
        }
        [class*="st-key-target-drive-card-"]:has(input:checked) {
            border-color: rgba(106,163,124,.8) !important;
            background: rgba(106,163,124,.08) !important;
            box-shadow: inset 3px 0 0 var(--accent);
        }
        [class*="st-key-target-drive-card-"] [data-testid="stCheckbox"] label {
            font-family: "Cascadia Mono", "Consolas", monospace;
            font-size: 15px;
            font-weight: 800;
        }
        .target-summary {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 10px;
        }
        .target-summary .target-label {
            display: block;
            color: var(--muted);
            font-size: 10px;
            font-weight: 800;
            letter-spacing: .04em;
            line-height: 1.2;
            text-transform: uppercase;
        }
        .target-summary .target-value {
            display: block;
            color: var(--text);
            font: 700 12px/1.35 "Cascadia Mono", "Consolas", monospace;
            margin-top: 4px;
            overflow-wrap: anywhere;
        }
        .stButton > button {
            border: 1px solid var(--line);
            border-radius: 5px;
            box-shadow: none;
            background: var(--card);
            color: var(--text);
            font-weight: 700;
            min-height: 38px;
            padding: 8px 12px;
            line-height: 1.25;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            white-space: normal;
            transition: border-color 160ms ease, background 160ms ease, color 160ms ease, transform 160ms ease;
        }
        .stButton > button:hover {
            border-color: rgba(106,163,124,.62);
            background: var(--card-alt);
            color: var(--accent);
            transform: translateY(-1px);
        }
        [data-testid="stSidebar"] {
            background: var(--sidebar);
            border-right: 1px solid var(--line);
            padding-top: .55rem;
        }
        [data-testid="stSidebar"] * {
            color: var(--text);
            line-height: 1.32;
        }
        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            gap: .55rem;
        }
        [data-testid="stSidebar"] .sidebar-title {
            border-bottom: 1px solid var(--line);
            color: var(--text);
            font-size: 12px;
            font-weight: 800;
            letter-spacing: .04em;
            text-transform: uppercase;
            padding-bottom: 8px;
            margin-bottom: 4px;
        }
        [data-testid="stSidebar"] .filter-group-title {
            border: 1px solid var(--line);
            border-radius: 5px;
            background: var(--field);
            color: var(--text);
            font-size: 12px;
            font-weight: 800;
            line-height: 1.25;
            min-height: 32px;
            padding: 8px 10px;
            margin: 10px 0 4px 0;
            transition: border-color 160ms ease, background 160ms ease;
        }
        [data-testid="stCheckbox"] {
            margin-top: 0;
            margin-bottom: 0;
        }
        [data-testid="stCheckbox"] label {
            min-height: 24px;
            display: flex;
            align-items: center;
            font-size: 12px;
        }
        label, .stCaptionContainer, p, span, div {
            color: inherit;
        }
        .stCaptionContainer, [data-testid="stCaptionContainer"] {
            color: var(--muted);
            line-height: 1.35;
        }
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stMarkdownContainer"] li,
        [data-testid="stMarkdownContainer"] span {
            color: var(--text-soft);
            line-height: 1.38;
        }
        div[data-testid="stDataFrame"] {
            border: 1px solid var(--line);
            border-radius: 5px;
            overflow: visible;
            background: var(--panel);
            margin-top: 5px;
            margin-bottom: 14px;
        }
        div[data-testid="stDataFrame"] * {
            color: var(--text);
            line-height: 1.25;
        }
        div[data-testid="stDataFrame"] [role="row"]:hover {
            background: rgba(95,159,179,.06) !important;
        }
        div[data-baseweb="input"],
        div[data-baseweb="select"] > div,
        div[data-baseweb="popover"] div {
            background: var(--field) !important;
            border-color: var(--line) !important;
            color: var(--text) !important;
            min-height: 34px;
            line-height: 1.28 !important;
        }
        input, textarea {
            background: var(--field) !important;
            color: var(--text) !important;
            border-color: var(--line) !important;
            min-height: 34px;
            line-height: 1.28 !important;
            padding-top: 6px !important;
            padding-bottom: 6px !important;
        }
        div[role="radiogroup"] label,
        div[role="tablist"] button {
            color: var(--text-soft) !important;
            border-color: transparent !important;
            background: transparent !important;
            border-radius: 5px 5px 0 0 !important;
            min-height: 34px;
            padding: 7px 10px !important;
            line-height: 1.25 !important;
            display: inline-flex !important;
            align-items: center !important;
            font-size: 13px !important;
            position: relative;
            transition: color 160ms ease, background 160ms ease, border-color 160ms ease;
        }
        div[role="tablist"] button[aria-selected="true"] {
            color: var(--accent) !important;
            border-color: var(--line) !important;
            border-bottom-color: var(--accent) !important;
            background: var(--accent-faint) !important;
        }
        div[role="tablist"] button[aria-selected="true"]::after {
            animation: tab-line 180ms ease-out both;
            background: var(--accent);
            bottom: -1px;
            content: "";
            height: 2px;
            left: 7px;
            position: absolute;
            right: 7px;
        }
        div[role="tablist"] {
            border-bottom: 1px solid var(--line-soft);
            gap: 4px;
            margin-top: 10px;
            margin-bottom: 12px;
        }
        div[role="slider"] {
            background: var(--accent) !important;
        }
        .stSlider [data-baseweb="slider"] {
            padding-top: 8px;
            padding-bottom: 6px;
        }
        .stSlider [data-baseweb="slider"] div {
            color: var(--text) !important;
        }
        [data-testid="stRadio"] label p,
        [data-testid="stSlider"] label,
        [data-testid="stNumberInput"] label,
        [data-testid="stTextInput"] label {
            color: var(--text-soft) !important;
            line-height: 1.3 !important;
            margin-bottom: 4px;
            font-size: 12px !important;
        }
        [data-testid="stAlert"] {
            background: #101a16;
            border: 1px solid rgba(106,163,124,.24);
            color: var(--text);
            line-height: 1.35;
        }
        .stProgress > div > div > div > div {
            background-color: var(--accent) !important;
        }
        .open-link-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 8px;
            margin: 8px 0 14px 0;
        }
        .open-link {
            display: block;
            background: var(--card);
            color: var(--text) !important;
            padding: 9px 10px;
            text-decoration: none !important;
            font-weight: 650;
            box-shadow: none;
            overflow-wrap: anywhere;
            line-height: 1.3;
            font-size: 13px;
        }
        .open-link:hover {
            border-color: rgba(106,163,124,.58);
            color: var(--accent) !important;
            background: var(--card-alt);
        }
        [data-testid="stExpander"] {
            border-color: var(--line) !important;
            transition: border-color 160ms ease, background 160ms ease;
        }
        [data-testid="stExpander"]:has(details[open]) {
            border-color: rgba(106,163,124,.58) !important;
            background: rgba(106,163,124,.04) !important;
        }
        @media (max-width: 760px) {
            .target-summary {
                grid-template-columns: 1fr;
                gap: 8px;
            }
        }
        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
                animation-duration: .01ms !important;
                animation-iteration-count: 1 !important;
                scroll-behavior: auto !important;
                transition-duration: .01ms !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def read_progress(path: str) -> dict:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def card(label: str, value: str, sub: str = "") -> str:
    return (
        '<div class="storage-card">'
        f'<div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(str(value))}</div>'
        f'<div class="sub">{html.escape(str(sub))}</div>'
        "</div>"
    )


def section_label(text: str) -> None:
    st.markdown(f'<div class="section-label">{html.escape(text)}</div>', unsafe_allow_html=True)


def chip(label: str, value: str | int | None) -> str:
    display = "" if value is None else str(value)
    return (
        '<div class="status-chip">'
        f'<span class="chip-label">{html.escape(label)}</span>'
        f'<span class="chip-value">{html.escape(display)}</span>'
        "</div>"
    )


def render_header(scan: dict | None, status: dict) -> None:
    scan = scan or {}
    chips = [
        chip("Last scan ID", status.get("scan_id") or scan.get("id") or "none"),
        chip("Mode", status.get("mode") or scan.get("mode") or "idle"),
        chip("Status", status.get("status") or scan.get("status") or "idle"),
        chip("Files scanned", f"{int(status.get('files_scanned') or scan.get('files_scanned') or 0):,}"),
        chip("Duplicate groups", f"{int(status.get('duplicate_groups_found') or scan.get('duplicate_groups') or 0):,}"),
    ]
    st.markdown(
        """
        <section class="tech-header">
          <div class="tech-header-top">
            <div>
              <h1>Storage Optimisation</h1>
              <p>Read-only duplicate and storage analysis.</p>
            </div>
            <div class="status-chip-row">
        """
        + "".join(chips)
        + """
            </div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def rows_to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def add_size_text(rows: list[dict], source_key: str = "total_bytes", out_key: str = "total_size") -> list[dict]:
    out = []
    for row in rows:
        item = dict(row)
        item[out_key] = human_bytes(int(item.get(source_key) or 0))
        out.append(item)
    return out


def annotate_rows(cache: StorageCache, scan_id: int, rows: list[dict], config: dict) -> list[dict]:
    if not rows:
        return []
    related = cache.related_rows_for_labels(scan_id, rows)
    enriched, _ = enrich_files(related, config)
    by_path = {row["path"]: row for row in enriched}
    return [by_path.get(row["path"], row) for row in rows]


def render_cards(metrics: dict) -> None:
    html_out = '<div class="card-grid">'
    html_out += card("Total scanned size", human_bytes(metrics.get("bytes_scanned", 0)), f"{metrics.get('files_scanned', 0):,} files")
    html_out += card("Duplicate size", human_bytes(metrics.get("duplicate_review_bytes", 0)), "safe to review manually")
    html_out += card("Duplicate groups", f"{metrics.get('duplicate_groups', 0):,}", "matched by SHA-256")
    html_out += card("Biggest duplicate group", human_bytes(metrics.get("biggest_duplicate_group_bytes", 0)), "space involved")
    html_out += card("Scan errors", f"{metrics.get('errors', 0):,}", "permission denied / skipped logged")
    html_out += card("Scan status", metrics.get("status", "idle"), metrics.get("completed_at") or metrics.get("started_at") or "")
    html_out += "</div>"
    st.markdown(html_out, unsafe_allow_html=True)


def render_mode_cards() -> None:
    html_out = '<div class="mode-grid">'
    for key, info in SCAN_MODES.items():
        html_out += (
            '<div class="mode-card">'
            f'<strong>{html.escape(info["label"])}</strong>'
            f'<span>{html.escape(info["summary"])}</span>'
            f'<small>{html.escape(info["risk"])}</small>'
            "</div>"
        )
    html_out += "</div>"
    st.markdown(html_out, unsafe_allow_html=True)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def human_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def scan_eta(status: dict) -> str:
    if status.get("status") != "running":
        return "done"
    started = parse_time(status.get("started_at"))
    ratio = status.get("progress_ratio")
    if not started or not isinstance(ratio, (int, float)) or ratio < 0.005:
        return "still learning"
    elapsed = (datetime.now(timezone.utc).astimezone() - started).total_seconds()
    remaining = (elapsed / max(float(ratio), 0.001)) - elapsed
    return human_duration(remaining)


def scan_rates(status: dict) -> tuple[str, str, str]:
    stored_duration = status.get("duration_seconds")
    if status.get("status") not in {"running", "stalled", "stopping"} and isinstance(stored_duration, (int, float)) and stored_duration > 0:
        elapsed = max(1.0, float(stored_duration))
    else:
        started = parse_time(status.get("started_at"))
        if not started:
            return "unknown", "unknown", "unknown"
        elapsed = max(1.0, (datetime.now(timezone.utc).astimezone() - started).total_seconds())
    files = int(status.get("files_scanned") or 0)
    bytes_scanned = int(status.get("bytes_scanned") or 0)
    return human_duration(elapsed), f"{files / elapsed:.1f}/s", f"{human_bytes(bytes_scanned / elapsed)}/s"


def render_progress(status: dict) -> None:
    ratio = status.get("progress_ratio")
    phase = status.get("phase") or status.get("status") or "idle"
    percent_text = "Percent unknown yet."
    if isinstance(ratio, (int, float)):
        ratio = max(0.0, min(float(ratio), 1.0))
        percent_text = f"estimated {ratio * 100:.1f}%"
        st.progress(ratio, text=f"Phase progress: {percent_text}")
    elif status.get("status") == "running":
        st.progress(0.0, text="Scan progress: working, percent unknown yet.")
    elapsed, files_per_second, bytes_per_second = scan_rates(status)
    updated_at = parse_time(status.get("updated_at"))
    if updated_at:
        age = human_duration((datetime.now(timezone.utc).astimezone() - updated_at).total_seconds())
    else:
        age = "unknown"
    worker = "yes" if status.get("pid_running") else "no"
    running_dot = '<span class="status-dot" aria-hidden="true"></span>' if status.get("status") == "running" else ""
    heartbeat_age = status.get("heartbeat_age_seconds")
    heartbeat_text = human_duration(heartbeat_age) if heartbeat_age is not None else "unknown"
    if status.get("status") == "stalled":
        st.warning(status.get("message") or "Scan may be stalled.")
    if status.get("status") == "failed":
        st.error(
            "Scan failed. "
            + str(status.get("message") or status.get("last_error_message") or "No exception text saved.")
        )
    st.markdown(
        f"""
        <div class="pulse-box">
        <b>{running_dot}{html.escape(str(status.get('status', 'idle')))}</b> | Phase: {html.escape(str(phase))} | Worker alive: {worker}<br>
        Mode: {html.escape(str(status.get('mode') or ''))}<br>
        Drive: {html.escape(str(status.get('current_drive') or ''))}<br>
        Folder: {html.escape(str(status.get('current_folder') or ''))}<br>
        File: {html.escape(str(status.get('current_file') or ''))}<br>
        Files discovered/indexed/hashed: {int(status.get('files_discovered') or 0):,} / {int(status.get('files_indexed') or status.get('files_scanned') or 0):,} / {int(status.get('files_hashed') or 0):,}<br>
        Files skipped/errors: {int(status.get('files_skipped') or 0):,} / {int(status.get('errors') or 0):,}<br>
        Files scanned: {int(status.get('files_scanned') or 0):,} ({html.escape(files_per_second)})<br>
        Data indexed: {human_bytes(status.get('bytes_scanned') or 0)} ({html.escape(bytes_per_second)})<br>
        Time run: {html.escape(elapsed)} | Time left: {html.escape(scan_eta(status))}<br>
        Last heartbeat: {html.escape(str(status.get('heartbeat_at') or status.get('updated_at') or ''))} ({html.escape(heartbeat_text)} ago)<br>
        Last error: {html.escape(str(status.get('last_error_phase') or ''))} | {html.escape(str(status.get('last_error_path') or ''))}<br>
        Error detail: {html.escape(str(status.get('last_error_message') or status.get('message') or ''))}<br>
        Duplicate groups found: {int(status.get('duplicate_groups_found') or 0):,}<br>
        Estimated space involved: {human_bytes(status.get('estimated_space_involved') or 0)}<br>
        Progress: {html.escape(percent_text)}
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.fragment(run_every="2s")
def render_live_progress(config: dict) -> None:
    render_progress(scan_status(config))


def mode_roots(config: dict, mode: str) -> list[str]:
    cache = StorageCache(config["database_path"])
    try:
        scanner = StorageScanner(config, cache)
        return scanner.roots_for_mode(mode)
    finally:
        cache.close()


def scan_target_drive_rows(config: dict) -> list[dict]:
    details = {
        item["drive"]: item
        for item in discover_windows_drive_details(bool(config.get("include_network_drives", False)))
    }
    preferred = ["C", config["backup_drive"], config["portable_drive"]]
    letters = list(dict.fromkeys([*preferred, *sorted(details)]))
    rows = []
    for drive in letters:
        detail = details.get(drive)
        roles = []
        if drive == config["backup_drive"]:
            roles.append("backup drive")
        if drive == config["portable_drive"]:
            roles.append("portable drive")
        rows.append(
            {
                "drive": drive,
                "root": f"{drive}:\\",
                "connected": detail is not None,
                "type": detail["type"] if detail else "missing",
                "roles": roles,
            }
        )
    return rows


def drive_status_text(row: dict) -> str:
    parts = ["connected", row["type"]] if row["connected"] else ["missing"]
    parts.extend(row["roles"])
    return " | ".join(parts)


def root_label(root: str) -> str:
    text = str(root)
    if len(text) >= 2 and text[1] == ":":
        return text[:2].upper()
    return Path(text).name or text


def render_fixed_target_cards(rows: list[dict]) -> None:
    cards = []
    for row in rows:
        cards.append(
            '<div class="target-fixed-card">'
            f'<strong>{html.escape(row["drive"])}:</strong>'
            f'<span>{html.escape(drive_status_text(row))}</span>'
            "</div>"
        )
    st.markdown(f'<div class="scan-target-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def render_selectable_drive_targets(rows: list[dict], mode: str, config: dict) -> list[str]:
    default_drives = (
        {config["backup_drive"], config["portable_drive"]}
        if mode == "media_only"
        else {row["drive"] for row in rows if row["connected"]}
    )
    columns = st.columns(min(4, max(1, len(rows))))
    selected = []
    for index, row in enumerate(rows):
        with columns[index % len(columns)]:
            key = f"scan_target_{mode}_{row['drive']}"
            with st.container(border=True, key=f"target-drive-card-{mode}-{row['drive'].lower()}"):
                checked = st.checkbox(
                    f"{row['drive']}:",
                    value=row["connected"] and row["drive"] in default_drives,
                    key=key,
                    disabled=not row["connected"],
                )
                st.caption(drive_status_text(row))
        if checked:
            selected.append(row["root"])
    return selected


def render_selected_targets(mode: str, roots: list[str]) -> None:
    selected_drives = ", ".join(root_label(root) for root in roots) or "none"
    paths = "<br>".join(html.escape(str(root)) for root in roots) or "none"
    st.markdown(
        '<div class="panel target-summary">'
        '<div><span class="target-label">Selected scan mode</span>'
        f'<span class="target-value">{html.escape(SCAN_MODES[mode]["label"])}</span></div>'
        '<div><span class="target-label">Selected drives to scan</span>'
        f'<span class="target-value">{html.escape(selected_drives)}</span></div>'
        '<div><span class="target-label">Final resolved scan paths</span>'
        f'<span class="target-value">{paths}</span></div>'
        "</div>",
        unsafe_allow_html=True,
    )


def available_drive_rows(config: dict, scanned_rows: list[dict]) -> list[dict]:
    scanned = {row["drive"]: row for row in scanned_rows}
    roots = discover_windows_drives(bool(config.get("include_network_drives", False)))
    wanted = sorted(set(roots + [f"{config['backup_drive']}:\\", f"{config['portable_drive']}:\\"] + [f"{drive}:\\" for drive in scanned]))
    result = []
    for root in wanted:
        drive = root.rstrip("\\").rstrip(":").upper()
        try:
            if drive == "TEST":
                raise OSError("virtual test drive")
            usage = shutil.disk_usage(root)
            status = "connected"
            total = usage.total
            free = usage.free
            used = usage.used
        except OSError:
            status = "virtual test data" if drive == "TEST" else "missing"
            total = free = used = 0
        scan = scanned.get(drive, {})
        result.append(
            {
                "drive": drive,
                "status": status,
                "total_capacity": human_bytes(total),
                "used_space": human_bytes(used),
                "free_space": human_bytes(free),
                "scanned_file_count": int(scan.get("file_count") or 0),
                "scanned_size": human_bytes(int(scan.get("total_bytes") or 0)),
            }
        )
    return result


def render_table(title: str, df: pd.DataFrame, columns: list[str], height: int = 380) -> None:
    st.subheader(title)
    if df.empty:
        st.info("No rows.")
        return
    existing = [col for col in columns if col in df.columns]
    config = {
        "folder_url": st.column_config.LinkColumn("Open in Explorer", display_text="Open folder"),
        "file_url": st.column_config.LinkColumn("Open file", display_text="Open file"),
        "open_folder": st.column_config.LinkColumn("Open in Explorer", display_text="Open folder"),
        "size_human": st.column_config.TextColumn("Size", width="small"),
        "labels": st.column_config.TextColumn("Labels", width="large"),
        "folder": st.column_config.TextColumn("Folder", width="large"),
        "path": st.column_config.TextColumn("Path", width="large"),
    }
    st.dataframe(
        df[existing],
        use_container_width=True,
        hide_index=True,
        height=height,
        row_height=32,
        column_config={key: value for key, value in config.items() if key in existing},
    )


def render_folder_links(rows: list[dict]) -> None:
    st.subheader("Open in Explorer")
    links = []
    for row in rows[:12]:
        url = str(row.get("open_folder") or path_uri(row.get("folder")))
        folder = str(row.get("folder") or "")
        if not url:
            continue
        name = folder.split("\\")[-1] or folder
        links.append(
            f'<a class="open-link" href="{html.escape(url)}" target="_blank">'
            f'Open in Explorer: {html.escape(name)}</a>'
        )
    if not links:
        st.info("No links.")
        return
    st.markdown(f'<div class="open-link-grid">{"".join(links)}</div>', unsafe_allow_html=True)


def _sidebar_checkbox_group(title: str, values: list[str], key_prefix: str) -> list[str]:
    selected = []
    values = [str(value) for value in values]

    st.sidebar.markdown(f'<div class="filter-group-title">{html.escape(title)}</div>', unsafe_allow_html=True)
    if not values:
        st.sidebar.caption("No values found.")
        return selected

    for value in values:
        safe_key = f"{key_prefix}_{value}".replace(" ", "_").replace(".", "dot").replace(":", "")
        if st.sidebar.checkbox(value, value=True, key=safe_key):
            selected.append(value)

    return selected


def sidebar_filters(cache: StorageCache, scan_id: int) -> tuple[dict, int, int]:
    st.sidebar.markdown('<div class="sidebar-title">Results Filters</div>', unsafe_allow_html=True)
    st.sidebar.caption("These filters only affect displayed results. They do not change scan targets.")

    drives = cache.distinct_values(scan_id, "drive")
    extensions = [ext or "(none)" for ext in cache.distinct_values(scan_id, "extension")]

    selected_drives = _sidebar_checkbox_group("Drive", drives, "drive_filter")
    selected_exts = _sidebar_checkbox_group("Extension", extensions, "extension_filter")

    folder_query = st.sidebar.text_input("Folder contains", "")
    search = st.sidebar.text_input("Filename/path search", "")
    min_size_mb = st.sidebar.number_input("Min size MB", min_value=0.0, value=0.0, step=100.0)
    page_size = st.sidebar.selectbox("Rows per page", [100, 250, 500], index=0)
    page = st.sidebar.number_input("Page", min_value=1, value=1, step=1)

    filters = {
        "drives": selected_drives,
        "extensions": ["" if item == "(none)" else item for item in selected_exts],
        "folder_query": folder_query,
        "search": search,
        "min_size": int(min_size_mb * 1024 * 1024),
    }
    return filters, int(page_size), int(page)


def duplicate_groups_for_table(rows: list[dict]) -> list[dict]:
    out = []
    for idx, row in enumerate(rows, start=1):
        item = dict(row)
        item["group_id"] = idx
        item["file_size"] = human_bytes(int(item.get("file_size_bytes") or 0))
        item["wasted_space"] = human_bytes(int(item.get("wasted_bytes") or 0))
        item["involved_space"] = human_bytes(int(item.get("involved_bytes") or 0))
        out.append(item)
    return out


def folder_summary_for_table(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        item = dict(row)
        item["total_size"] = human_bytes(int(item.get("total_bytes") or 0))
        item["open_folder"] = path_uri(item.get("folder"))
        out.append(item)
    return out


def possible_duplicate_folder_rows(folder_rows: list[dict]) -> list[dict]:
    buckets: dict[tuple[int, int], list[dict]] = {}
    for row in folder_rows:
        key = (int(row.get("file_count") or 0), int(row.get("total_bytes") or 0))
        buckets.setdefault(key, []).append(row)
    out = []
    for (count, total), members in buckets.items():
        if count <= 0 or len(members) < 2:
            continue
        for member in members:
            item = dict(member)
            item["signature"] = f"{count} files / {human_bytes(total)}"
            item["matching_folder_count"] = len(members)
            item["total_size"] = human_bytes(total)
            item["open_folder"] = path_uri(item.get("folder"))
            out.append(item)
    return out


def diagnostics_rows(
    config: dict,
    cache: StorageCache,
    scan_id: int | None,
    drive_rows: list[dict],
    live_status: dict,
) -> tuple[list[dict], list[dict]]:
    errors = cache.error_rows(scan_id, limit=20) if scan_id else []
    error_count = cache.error_count(scan_id) if scan_id else 0
    latest_error = cache.latest_error(scan_id) if scan_id else None
    db_info = cache.database_info()
    scan = cache.scan_row(scan_id) if scan_id else {}
    project_usage = shutil.disk_usage(str(PROJECT_ROOT.anchor or PROJECT_ROOT.drive or PROJECT_ROOT))
    b_status = next((row["status"] for row in drive_rows if row["drive"] == config["backup_drive"]), "missing")
    p_status = next((row["status"] for row in drive_rows if row["drive"] == config["portable_drive"]), "missing")
    rows = [
        {"name": "Python version", "value": platform.python_version()},
        {"name": "App version", "value": __version__},
        {"name": "Database path", "value": config["database_path"]},
        {"name": "Latest scan ID", "value": scan_id or ""},
        {"name": "Latest scan status", "value": (scan or {}).get("status") or live_status.get("status", "")},
        {"name": "Indexed files", "value": (scan or {}).get("files_scanned", 0)},
        {"name": "Drives detected", "value": ", ".join(row["drive"] for row in drive_rows if row["status"] == "connected")},
        {"name": "B: status", "value": b_status},
        {"name": "P: status", "value": p_status},
        {"name": "Scan errors", "value": error_count},
        {"name": "Files skipped", "value": (scan or {}).get("files_skipped", 0)},
        {"name": "Last error path", "value": (latest_error or {}).get("path", "")},
        {"name": "Last error phase", "value": (latest_error or {}).get("phase", "")},
        {"name": "Last error message", "value": (latest_error or {}).get("error_message", "")},
        {"name": "Database size", "value": human_bytes(db_info["size_bytes"])},
        {"name": "SQLite journal mode", "value": db_info["journal_mode"]},
        {"name": "Cache hits", "value": (scan or {}).get("cache_hits", 0)},
        {"name": "Partial hash candidates", "value": (scan or {}).get("partial_hash_candidates", 0)},
        {"name": "Full hash candidates", "value": (scan or {}).get("full_hash_candidates", 0)},
        {"name": "Files hashed", "value": (scan or {}).get("files_hashed", 0)},
        {"name": "Scan duration", "value": human_duration((scan or {}).get("duration_seconds"))},
        {"name": "Avg files/sec", "value": f"{float((scan or {}).get('avg_files_per_second') or 0):.1f}"},
        {"name": "Avg MB/sec", "value": f"{float((scan or {}).get('avg_megabytes_per_second') or 0):.2f}"},
        {"name": "Project drive free", "value": human_bytes(project_usage.free)},
    ]
    return rows, errors[-20:]


def main() -> None:
    add_css()
    config = load_config()
    status = scan_status(config)

    header_cache = StorageCache(config["database_path"])
    try:
        header_scan_id = header_cache.latest_scan_id()
        header_scan = dict(header_cache.scan_row(header_scan_id)) if header_scan_id else None
    finally:
        header_cache.close()

    render_header(header_scan, status)

    section_label("Scan Control")
    render_mode_cards()
    mode_keys = list(SCAN_MODES.keys())
    active_mode = status.get("mode")
    mode_index = mode_keys.index(active_mode) if active_mode in mode_keys else 0
    mode = st.radio(
        "Scan mode",
        options=mode_keys,
        index=mode_index,
        format_func=lambda item: SCAN_MODES[item]["label"],
        horizontal=True,
    )

    section_label("Scan Target Selection")
    target_rows = scan_target_drive_rows(config)
    if mode == "test":
        roots = mode_roots(config, mode)
        st.markdown(
            '<div class="panel"><b>Test data only</b><br>'
            + html.escape(", ".join(roots))
            + "</div>",
            unsafe_allow_html=True,
        )
    elif mode == "backup_vs_portable":
        roots = mode_roots(config, mode)
        fixed_rows = [
            row
            for row in target_rows
            if row["drive"] in {config["backup_drive"], config["portable_drive"]}
        ]
        render_fixed_target_cards(fixed_rows)
    else:
        roots = render_selectable_drive_targets(target_rows, mode, config)
    render_selected_targets(mode, roots)

    missing_roles = [
        f'{row["drive"]}:'
        for row in target_rows
        if row["drive"] in {config["backup_drive"], config["portable_drive"]} and not row["connected"]
    ]
    if missing_roles:
        st.warning(f"Missing or disconnected: {', '.join(missing_roles)}. It cannot be scanned.")
    if not roots:
        st.warning("Choose at least one connected drive before Start scan.")
    full_confirmed = True
    if mode == "full":
        st.warning("Full scan can take hours. It scans selected fixed/removable drives and writes only app cache/report files.")
        full_confirmed = st.checkbox("I understand, start full scan")

    col_a, col_b, col_c, col_d = st.columns([1, 1, 1, 1])
    with col_a:
        start_disabled = status.get("status") in {"running", "stalled", "stopping"} or not roots or (mode == "full" and not full_confirmed)
        if st.button("Start scan", use_container_width=True, disabled=start_disabled):
            start_background_scan(mode, roots, config)
            st.rerun()
    with col_b:
        if st.button("Stop Scan", use_container_width=True, disabled=status.get("status") not in {"running", "stalled", "stopping"}):
            request_stop(config)
            st.rerun()
    with col_c:
        if st.button("Export Excel", use_container_width=True):
            output = export_excel_report()
            st.success(f"Excel report: {output}")
    with col_d:
        if st.button("Export CSV", use_container_width=True):
            output = export_csv_report()
            st.success(f"CSV report folder: {output}")

    section_label("Scan Status")
    if status.get("status") == "running":
        render_live_progress(config)
    else:
        render_progress(status)

    cache = StorageCache(config["database_path"])
    try:
        scan_id = cache.latest_scan_id()
        scan = cache.scan_row(scan_id) if scan_id else None
        if scan_id is None or scan is None:
            st.info("No scan yet. Test scan is default.")
            return

        scan_metrics = dict(scan)
        section_label("Storage Summary")
        render_cards(scan_metrics)

        drive_summary = add_size_text(cache.drive_summary_rows(scan_id))
        drive_capacity = available_drive_rows(config, drive_summary)
        diagnostics, recent_errors = diagnostics_rows(config, cache, scan_id, drive_capacity, status)
        filters, page_size, page = sidebar_filters(cache, scan_id)
        total_filtered = cache.file_rows_count(scan_id, filters)
        offset = (page - 1) * page_size
        page_rows = annotate_rows(
            cache,
            scan_id,
            cache.file_rows_filtered(scan_id, filters, limit=page_size, offset=offset),
            config,
        )

        duplicate_rows = annotate_rows(cache, scan_id, cache.duplicate_file_rows(scan_id, limit=500), config)
        portable_drive = config["portable_drive"]
        backup_drive = config["backup_drive"]
        p_on_b = annotate_rows(cache, scan_id, cache.p_files_on_b_rows(scan_id, portable_drive, backup_drive), config)
        p_not_on_b = annotate_rows(cache, scan_id, cache.p_files_not_on_b_rows(scan_id, portable_drive, backup_drive), config)
        old_editing = annotate_rows(
            cache,
            scan_id,
            cache.old_editing_rows(scan_id, portable_drive, config.get("possible_old_editing_extensions", []), 200),
            config,
        )
        large_not_b = annotate_rows(
            cache,
            scan_id,
            cache.file_rows_filtered(
                scan_id,
                {"min_size": int(float(config.get("large_file_threshold_mb", 500)) * 1024 * 1024)},
                limit=200,
            ),
            config,
        )
        large_not_b = [row for row in large_not_b if not row.get("exact_duplicate_exists_on_b")]

        folder_rows = folder_summary_for_table(cache.folder_summary_rows(scan_id, limit=500))
        duplicate_folder_rows = possible_duplicate_folder_rows(folder_rows)
        takeout_a = cache.file_rows_filtered(scan_id, {"search": "takeout"}, limit=300)
        takeout_b = cache.file_rows_filtered(
            scan_id,
            {"extension_family": [".zip", ".json", ".jpg", ".jpeg", ".mp4", ".mov", ".heic", ".dng"]},
            limit=300,
        )
        takeout_by_path = {row["path"]: row for row in [*takeout_a, *takeout_b]}
        takeout_rows = annotate_rows(cache, scan_id, list(takeout_by_path.values()), config)

        st.markdown(
            f'<div class="panel"><b>Latest scan</b><br>'
            f'ID {scan_id} | {html.escape(str(scan.get("mode", "")))} | '
            f'{html.escape(str(scan.get("status", "")))} | {html.escape(str(scan.get("completed_at", "")))}'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="panel"><b>Scan health</b><br>'
            f'{int(scan.get("errors") or 0):,} errors | {int(scan.get("files_skipped") or 0):,} skipped. '
            f'Last error phase: {html.escape(str(scan.get("last_error_phase") or ""))}. '
            f'Last error path: {html.escape(str(scan.get("last_error_path") or ""))}.'
            f'</div>',
            unsafe_allow_html=True,
        )

        section_label("Tables / Analysis")
        tab_overview, tab_priorities, tab_duplicates, tab_space, tab_takeout, tab_all, tab_diag = st.tabs(
            ["Overview", "Review priorities", "Duplicates", "Space", "Google Takeout", "All files", "Diagnostics"]
        )
        with tab_overview:
            render_table("Drive capacity and scanned size", rows_to_df(drive_capacity), [
                "drive", "status", "total_capacity", "used_space", "free_space", "scanned_file_count", "scanned_size"
            ], 330)
            render_table("Space by drive", rows_to_df(drive_summary), ["drive", "file_count", "total_size", "total_bytes"], 260)
            render_table("Space by extension", rows_to_df(add_size_text(cache.extension_summary_rows(scan_id))), [
                "extension", "file_count", "total_size", "total_bytes"
            ], 360)
        with tab_priorities:
            st.caption("Top review priorities are information only. Use safe to review manually wording.")
            render_table("P files already backed up on B", rows_to_df(p_on_b), FILE_COLUMNS, 360)
            render_table("Biggest duplicate groups", rows_to_df(duplicate_groups_for_table(cache.duplicate_group_rows(scan_id, 100))), [
                "group_id", "file_count", "wasted_space", "involved_space", "drives", "sample_filename", "full_hash"
            ], 360)
            render_table("Old editing files on P", rows_to_df(old_editing), FILE_COLUMNS, 360)
            render_table("Large files not on B", rows_to_df(large_not_b), FILE_COLUMNS, 360)
        with tab_duplicates:
            render_table("Duplicate groups sorted by wasted space", rows_to_df(duplicate_groups_for_table(cache.duplicate_group_rows(scan_id, 500))), [
                "group_id", "file_count", "file_size", "wasted_space", "involved_space", "drives", "sample_filename", "full_hash"
            ], 440)
            render_table("Duplicate file rows", rows_to_df(duplicate_rows), FILE_COLUMNS, 460)
            render_table("Possible duplicate folders", rows_to_df(duplicate_folder_rows), [
                "signature", "folder", "open_folder", "file_count", "total_size", "matching_folder_count"
            ], 420)
        with tab_space:
            render_table("Top 50 biggest folders", rows_to_df(folder_rows[:50]), ["folder", "open_folder", "file_count", "total_size", "total_bytes"], 430)
            render_folder_links(folder_rows[:12])
            top_files = annotate_rows(cache, scan_id, cache.file_rows_filtered(scan_id, {}, limit=100), config)
            render_table("Top 100 biggest files", rows_to_df(top_files), FILE_COLUMNS, 520)
        with tab_takeout:
            st.caption("Google Takeout ZIPs, JSON metadata, and common extracted media are grouped here for review.")
            render_table("Google Takeout ZIPs and extracted files", rows_to_df(takeout_rows), FILE_COLUMNS, 520)
        with tab_all:
            st.caption(f"Showing {len(page_rows):,} of {total_filtered:,} matching rows from SQLite.")
            render_table("Filtered files", rows_to_df(page_rows), FILE_COLUMNS, 620)
        with tab_diag:
            if st.button("Export scan errors CSV", use_container_width=False):
                output = export_scan_errors_csv(scan_id=scan_id)
                st.success(f"Scan errors CSV: {output}")
            render_table("Diagnostics", rows_to_df(diagnostics), ["name", "value"], 360)
            render_table("Last error messages", rows_to_df(recent_errors), ["path", "phase", "error_type", "error_message", "created_at"], 420)
    finally:
        cache.close()


if __name__ == "__main__":
    main()
