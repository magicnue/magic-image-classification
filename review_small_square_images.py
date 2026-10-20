#!/usr/bin/env python3
"""Collect small, near-square images for manual avatar/sticker review.

Workflow:
  1. collect: move candidates into a review directory while preserving their
     relative directory tree and recording original absolute paths.
  2. The user removes true avatars/stickers from the review directory.
  3. restore: move every remaining candidate back to its exact original path.

No existing destination is ever overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
    ".tif", ".tiff", ".avif", ".heic",
}
MANIFEST_NAME = "review_manifest.json"
RESTORE_REPORT_NAME = "restore_report.json"


@dataclass
class ManifestEntry:
    original_path: str
    review_path: str
    relative_path: str
    width: int
    height: int
    size_bytes: int
    mtime_ns: int
    animated: bool
    has_alpha: bool


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def normalized(path: Path) -> Path:
    return path.expanduser().resolve()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def inspect_image(path: Path) -> tuple[int, int, bool, bool]:
    with Image.open(path) as image:
        width, height = image.size
        animated = bool(getattr(image, "is_animated", False))
        has_alpha = "A" in image.getbands() or "transparency" in image.info
    return width, height, animated, has_alpha


def candidate_reason(width: int, height: int, min_side: int, max_side: int, max_ratio: float) -> bool:
    short = min(width, height)
    long = max(width, height)
    if short < min_side or long > max_side:
        return False
    return long / short <= max_ratio


def iter_images(input_root: Path, review_root: Path):
    for current_root, dir_names, file_names in os.walk(input_root):
        current = Path(current_root)
        dir_names[:] = [
            name for name in dir_names
            if not is_relative_to(normalized(current / name), review_root)
        ]
        for name in file_names:
            path = current / name
            if path.suffix.lower() in IMAGE_EXTENSIONS:
                yield path


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    index = 1
    while True:
        candidate = path.with_name(f"{stem}__candidate_{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def collect(args: argparse.Namespace) -> int:
    input_root = normalized(Path(args.input))
    if not input_root.is_dir():
        raise RuntimeError(f"input directory not found: {input_root}")

    review_root = normalized(
        Path(args.review_dir) if args.review_dir
        else input_root / "_头像表情人工审核"
    )
    if review_root == input_root:
        raise RuntimeError("review directory cannot equal input directory")

    manifest_path = review_root / MANIFEST_NAME
    if manifest_path.exists():
        raise RuntimeError(
            f"an unfinished review manifest already exists: {manifest_path}\n"
            "Run restore first or choose another --review-dir."
        )

    scanned = 0
    candidates: list[tuple[Path, int, int, bool, bool]] = []
    unreadable: list[dict[str, str]] = []
    for path in iter_images(input_root, review_root):
        scanned += 1
        try:
            width, height, animated, has_alpha = inspect_image(path)
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            unreadable.append({"path": str(path), "error": str(exc)})
            continue
        if candidate_reason(width, height, args.min_side, args.max_side, args.max_ratio):
            candidates.append((path, width, height, animated, has_alpha))

    print(f"scanned: {scanned}")
    print(f"candidates: {len(candidates)}")
    print(f"unreadable: {len(unreadable)}")
    print(f"criteria: min_side>={args.min_side}, max_side<={args.max_side}, ratio<={args.max_ratio}")
    print(f"review: {review_root}")
    if args.dry_run:
        print("dry-run: no files moved")
        return 0

    review_root.mkdir(parents=True, exist_ok=True)
    entries: list[ManifestEntry] = []
    for source, width, height, animated, has_alpha in candidates:
        relative = source.relative_to(input_root)
        destination = unique_destination(review_root / "candidates" / relative)
        stat = source.stat()
        entries.append(ManifestEntry(
            original_path=str(source),
            review_path=str(destination),
            relative_path=str(relative),
            width=width,
            height=height,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            animated=animated,
            has_alpha=has_alpha,
        ))

    manifest = {
        "version": 1,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "input_root": str(input_root),
        "review_root": str(review_root),
        "criteria": {
            "min_side": args.min_side,
            "max_side": args.max_side,
            "max_ratio": args.max_ratio,
        },
        "entries": [asdict(entry) for entry in entries],
        "unreadable": unreadable,
    }
    # Persist the complete recovery map before moving the first file. If the
    # process is interrupted, restore can distinguish unmoved originals from
    # candidates that already reached the review directory.
    write_json_atomic(manifest_path, manifest)
    for index, entry in enumerate(entries, 1):
        source = Path(entry.original_path)
        destination = Path(entry.review_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        if index % 100 == 0 or index == len(entries):
            print(f"moved: {index}/{len(entries)}", flush=True)
    print(f"manifest: {manifest_path}")
    print("After manual review, remove true avatars/stickers, then run restore.")
    return 0


def restore(args: argparse.Namespace) -> int:
    review_root = normalized(Path(args.review_dir))
    manifest_path = review_root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    restored = 0
    removed_by_user = 0
    already_restored = 0
    conflicts: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for raw_entry in manifest.get("entries", []):
        source = normalized(Path(raw_entry["review_path"]))
        destination = normalized(Path(raw_entry["original_path"]))
        if not source.exists():
            if destination.exists():
                already_restored += 1
            else:
                removed_by_user += 1
            continue
        if destination.exists():
            conflicts.append({"review_path": str(source), "original_path": str(destination)})
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            restored += 1
        except OSError as exc:
            errors.append({"review_path": str(source), "original_path": str(destination), "error": str(exc)})

    report = {
        "restored_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "restored": restored,
        "removed_by_user": removed_by_user,
        "already_restored": already_restored,
        "conflicts": conflicts,
        "errors": errors,
    }
    report_path = review_root / RESTORE_REPORT_NAME
    write_json_atomic(report_path, report)
    print(f"restored: {restored}")
    print(f"removed_by_user: {removed_by_user}")
    print(f"already_restored: {already_restored}")
    print(f"conflicts: {len(conflicts)}")
    print(f"errors: {len(errors)}")
    print(f"report: {report_path}")
    if conflicts or errors:
        print("Review directory retained because unresolved files remain.")
        return 2
    print("Restore complete. Manifest/report are retained for audit.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reversibly collect small near-square images for manual avatar/sticker review."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect", help="Move candidates into a review directory.")
    collect_parser.add_argument("--input", "-i", required=True, help="Directory to scan recursively.")
    collect_parser.add_argument("--review-dir", help="Review directory. Default: <input>/_头像表情人工审核")
    collect_parser.add_argument("--min-side", type=int, default=32, help="Minimum short side. Default: 32.")
    collect_parser.add_argument("--max-side", type=int, default=640, help="Maximum long side. Default: 640.")
    collect_parser.add_argument("--max-ratio", type=float, default=1.25, help="Maximum long/short aspect ratio. Default: 1.25.")
    collect_parser.add_argument("--dry-run", action="store_true", help="Count candidates without moving files.")
    collect_parser.set_defaults(func=collect)

    restore_parser = subparsers.add_parser("restore", help="Restore candidates left after manual review.")
    restore_parser.add_argument("--review-dir", required=True, help="Review directory containing review_manifest.json.")
    restore_parser.set_defaults(func=restore)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "min_side", 1) < 1:
        parser.error("--min-side must be at least 1")
    if getattr(args, "max_side", 1) < getattr(args, "min_side", 1):
        parser.error("--max-side must be >= --min-side")
    if getattr(args, "max_ratio", 1.0) < 1.0:
        parser.error("--max-ratio must be >= 1.0")
    try:
        return args.func(args)
    except Exception as exc:
        eprint(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
