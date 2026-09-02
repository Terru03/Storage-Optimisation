# Storage Optimisation

A local, read-only Windows storage analyser for finding duplicate files, understanding disk usage, and checking whether files on one drive already exist on another.

Storage Optimisation is deliberately conservative. It scans and reports. It does **not** delete, move, rename, modify, or deduplicate your files automatically.

## What it does

- Scans selected folders, fixed drives, removable drives, and optionally network drives.
- Finds exact duplicate candidates using a staged pipeline: file size, partial hash, then full content hash.
- Reuses cached hashes for unchanged files so repeat scans are significantly faster.
- Compares a configurable portable/work drive against a configurable trusted backup drive.
- Provides a local Streamlit dashboard with drive selection, live progress, stop controls, diagnostics, filters, and report views.
- Reports largest files and folders, extension usage, old files, duplicate groups, scan errors, and backup/portable comparisons.
- Exports Excel and CSV reports for manual review.
- Keeps going past inaccessible, locked, missing, long-path, offline cloud-placeholder, and hashing-error files and records the issue instead of silently ignoring it.
- Supports a clean stop request and records scan state in SQLite/JSON so interrupted work is easier to diagnose and repeat.

## Safety model

The scanner is designed for inspection first.

It does not:

- delete files;
- move or rename files;
- modify file contents or metadata;
- follow symlinks or reparse points by default;
- scan the Recycle Bin by default;
- scan common Windows/system/cache folders by default.

Application state is written only under the project folder by default:

```text
data\
reports\
```

Original scanned files are opened only as needed for metadata and content hashing.

## Requirements

- Windows 10 or Windows 11
- Python 3.10+

Python packages:

- Streamlit
- pandas
- openpyxl

## Installation

```bat
git clone https://github.com/Terru03/Storage-Optimisation.git
cd Storage-Optimisation
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Local configuration is optional. To customise drive letters or scanning rules:

```bat
copy config.example.json config.json
```

`config.json` is ignored by Git and should contain machine-specific settings.

## Recommended first run

Start with the bundled synthetic test data:

```bat
run_scan.bat test
```

Then open the dashboard:

```bat
run_dashboard.bat
```

Only move on to real drives after the test scan and dashboard work correctly.

## Dashboard

Run:

```bat
run_dashboard.bat
```

The dashboard provides:

- explicit scan-target selection;
- live scan phase and progress;
- stop controls;
- drive and scan diagnostics;
- duplicate groups;
- biggest files and folders;
- drive-space and extension summaries;
- configurable backup-versus-portable views;
- scan-error review and CSV export;
- report export controls.

The scan-target controls decide what is actually scanned. Results filters only change what is displayed.

File and folder links use local `file:///` links and do not modify the target.

## Scan modes

### Test

```bat
run_scan.bat test
```

Scans the repository's `test_data` folder.

### Backup vs portable

```bat
run_scan.bat backup_vs_portable
```

Scans the two drive letters configured as `backup_drive` and `portable_drive`.

The default example uses:

```text
backup_drive   = B:
portable_drive = P:
```

These are only defaults and can be changed in `config.json`.

### Media only

```bat
run_scan.bat media_only
```

Scans detected drives but indexes only extensions listed in `media_only_extensions`.

### Full

```bat
run_scan.bat full
```

Scans detected fixed and removable drives after applying the configured exclusions and optional drive allowlist.

A full-drive scan can be I/O intensive. Keep external drives connected and the PC awake until the scan finishes or is stopped cleanly.

### Explicit roots

The CLI can scan only paths you choose, regardless of the automatic drive mode:

```bat
python -m storage_optimiser.cli scan --mode full --root "D:\Photos" --root "E:\Projects"
```

Repeat `--root` for each path.

## How duplicate detection works

The scanner avoids full-hashing every file unnecessarily.

1. Index file metadata into SQLite.
2. Group files by identical size.
3. Calculate a partial hash only for same-size candidates.
4. Full-hash only files that still match after the partial hash.
5. Group matching full hashes as exact duplicate candidates.

SHA-256 is the default hashing algorithm. Cached hashes are reused when path, size, and modification time have not changed.

This identifies exact byte-for-byte matches. It does not attempt perceptual photo/video similarity detection.

## Reports

Excel:

```bat
export_report.bat
```

CSV:

```bat
export_csv.bat
```

Or use the CLI:

```bat
python -m storage_optimiser.cli export --format both
```

CSV export can also include the complete file index:

```bat
python -m storage_optimiser.cli export --format csv --include-all-files
```

Reports are written under `reports\` by default and can include:

- summary statistics;
- duplicate groups and duplicate files;
- files on the portable drive also found on the backup drive;
- portable files not found on the backup drive;
- large files;
- old files;
- extension summaries;
- folder-size summaries;
- scan errors.

## Configuration

Copy `config.example.json` to `config.json` before changing local settings.

Important options include:

| Setting | Purpose |
|---|---|
| `backup_drive` | Drive treated as the trusted backup reference. |
| `portable_drive` | Drive compared against the backup reference. |
| `drive_allowlist` | Restrict automatic drive scans to selected drive letters. |
| `include_network_drives` | Include mapped/network drives in automatic discovery. |
| `include_system_folders` | Allow normally excluded Windows/system folders. |
| `scan_recycle_bin` | Include `$Recycle.Bin`. |
| `follow_symlinks` | Follow symlinks/reparse points. Disabled by default to avoid loops. |
| `media_only_extensions` | Extensions indexed by `media_only` mode. |
| `partial_hash_bytes` | Amount sampled from candidate files during partial hashing. |
| `hash_algorithm` | Content-hash algorithm, default `sha256`. |
| `full_hash_max_mb` | Optional size limit for full hashing. `0` means no size limit. |
| `full_hash_large_files` | Whether files above the optional full-hash limit may still be hashed. |
| `skip_files_above_mb` | Optional metadata/indexing skip threshold. `0` disables it. |
| `large_file_threshold_mb` | Threshold used for large-file reporting. |
| `old_file_days` | Age threshold used for old-file reporting. |
| `excluded_folder_names` | Folder names skipped during normal scans. |
| `excluded_path_fragments` | Case-insensitive path fragments skipped during normal scans. |
| `excluded_path_patterns` | Optional case-insensitive wildcard path exclusions. |

Project-generated databases, progress files, reports, and local configuration are excluded from Git by default.

## Scan states and errors

A scan can finish as:

- `complete`: finished without recorded scan issues;
- `complete_with_errors`: finished but one or more files/folders could not be processed;
- `stopped_by_user`: stopped cleanly after a stop request;
- `failed`: a fatal scanner error stopped the run.

Non-fatal problems are recorded with the affected path, phase, error type, and message. Re-running a scan can reuse cached hashes for unchanged files.

## Tests

The repository includes scanner resilience and scan-target tests using temporary/synthetic data.

Run them with Python's built-in `unittest` runner:

```bat
python -m unittest discover -s tests -p "test_*.py"
```

To generate a larger synthetic test tree:

```bat
python tests\generate_synthetic_dataset.py C:\temp\storage-scan-data --files 10000 --large-mb 64
```

The generator refuses to populate a non-empty output folder.

## Privacy

Storage Optimisation runs locally. There is no account system, telemetry service, cloud database, or automatic upload of filenames or hashes.

The dashboard is served locally by Streamlit. If you intentionally change Streamlit's network configuration or expose it beyond localhost, you are responsible for securing that deployment.

## Limitations

- Windows is the supported platform.
- A full scan of large disks can take a long time because exact duplicate confirmation requires reading candidate file contents.
- Offline/cloud-placeholder files may be skipped until they are available locally.
- Duplicate results are review information, not instructions to delete files.
- There is currently no packaged installer; the supported setup is Python plus the included batch launchers.

## Licence

Released under the MIT Licence. See `LICENSE`.
