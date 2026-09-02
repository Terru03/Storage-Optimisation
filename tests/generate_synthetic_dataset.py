"""Create an empty-target synthetic dataset for scanner scale checks."""

import argparse
from pathlib import Path


def write_dataset(output: Path, file_count: int, large_mb: int) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output folder must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    duplicate = b"duplicate candidate data\n"
    for index in range(file_count):
        folder = output / f"batch_{index % 20:02d}" / f"nested_{index % 7:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        payload = duplicate if index % 10 == 0 else f"same-size-{index:08d}\n".encode("ascii")
        (folder / f"file_{index:06d}.bin").write_bytes(payload)
    (output / "unicode_\u0219edin\u021b\u0103_\u6d4b\u8bd5.bin").write_bytes(duplicate)
    (output / "empty.bin").write_bytes(b"")
    long_folder = output
    for index in range(12):
        long_folder = long_folder / f"long_folder_{index:02d}_abcdefghijklmnopqrstuvwxyz"
    long_folder.mkdir(parents=True, exist_ok=True)
    (long_folder / "long_path_file.bin").write_bytes(duplicate)
    if large_mb:
        (output / "large_dummy.bin").write_bytes(b"0" * (large_mb * 1024 * 1024))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create scanner-only synthetic test data")
    parser.add_argument("output", type=Path)
    parser.add_argument("--files", type=int, default=1000)
    parser.add_argument("--large-mb", type=int, default=16)
    args = parser.parse_args()
    if args.files < 1 or args.large_mb < 0:
        raise ValueError("--files must be positive and --large-mb cannot be negative")
    write_dataset(args.output, args.files, args.large_mb)


if __name__ == "__main__":
    main()
