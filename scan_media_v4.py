#!/usr/bin/env python3
"""Read-only content discovery scanner for designing a new media taxonomy.

Unlike classify_media_v8.py, this script does not ask the model to select an
existing category. It asks for a compact, structured observation of visible
content and writes one row per media file to TSV. It never moves or copies
input files.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from classify_media_v8 import (
    DEFAULT_API_URL,
    DEFAULT_MAX_DIM,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    MediaItem,
    call_lmstudio,
    eprint,
    ensure_utf8_stdio,
    image_to_jpeg_bytes,
    is_ffmpeg_available,
    private_item_label,
    scan_media,
    video_frame_to_jpeg_bytes,
    worker_log_consumer,
)


ANALYSIS_VERSION = "content-discovery-v1"
DEFAULT_ANALYSIS_MAX_TOKENS = 180
ANALYSIS_PROMPT = """You are performing content discovery for a future media classification system.

Do not choose from any predefined category list. Do not use or imitate an
existing taxonomy. Describe only visible evidence in this media, without
inferring its source, author, intent, filename meaning, or hidden context.

Return ONLY one valid JSON object with exactly these string fields:
{
  "subjects": "main visible people, animals, objects, characters, or entities",
  "setting": "place, environment, situation, or scene",
  "media_form": "photo, illustration, anime, screenshot, meme, document, game UI, video frame, or other visible form",
  "visual_style": "real-world, drawn, 3D, pixel art, cinematic, diagrammatic, etc.",
  "text_or_ui": "none, captions, subtitles, chat, document, game HUD, webpage, or other visible text/UI",
  "sensitivity": "none, sexualized, nudity, gore, injury, disturbing, or uncertain",
  "themes": "comma-separated visible themes or motifs; do not force a fixed list",
  "description": "one concise sentence describing the visible content",
  "confidence": "high, medium, or low"
}

Use "none" when a field is not applicable. Keep values concise. JSON only."""
FIELDS = [
    "path", "kind", "status", "subjects", "setting", "media_form",
    "visual_style", "text_or_ui", "sensitivity", "themes", "description",
    "confidence", "tokens", "elapsed_seconds", "error", "raw_response",
]


@dataclass
class ObservationResult:
    item: MediaItem
    observation: dict[str, str]
    raw: str
    tokens: int | None
    status: str
    error: str | None = None
    elapsed: float = 0.0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only content discovery scan; output observations to TSV without using old categories."
    )
    parser.add_argument("--input", "-i", required=True, help="Directory to scan. Files are never moved or copied.")
    parser.add_argument("--report", help="TSV path. Defaults to <input>/_logs/content_observation_report_<timestamp>.tsv.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--include-videos", action="store_true")
    parser.add_argument("--video-seek", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=0, help="Maximum files to analyze; 0 means all.")
    parser.add_argument("--offset", type=int, default=0, help="Skip this many sorted files before applying --limit.")
    parser.add_argument("--max-dim", type=int, default=DEFAULT_MAX_DIM)
    parser.add_argument("--no-resize", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_ANALYSIS_MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--private-logs", action="store_true")
    parser.add_argument("--exclude-dir", action="append", default=[])
    return parser.parse_args(argv)


def parse_observation(raw: str) -> dict[str, str]:
    text = raw.strip().strip("`").strip()
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("model response did not contain a JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("model response JSON was not an object")
    result = {}
    for field in FIELDS[3:12]:
        item = value.get(field, "")
        if isinstance(item, list):
            item = ", ".join(str(x) for x in item)
        result[field] = str(item).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()
    return result


def analyze_item(item: MediaItem, args: argparse.Namespace) -> ObservationResult:
    started = time.perf_counter()
    try:
        if item.kind == "video":
            image_bytes, mime = video_frame_to_jpeg_bytes(item.path, args.video_seek, args.max_dim)
        else:
            image_bytes, mime = image_to_jpeg_bytes(item.path, args.max_dim, args.no_resize)
        raw, tokens = call_lmstudio(
            image_bytes, mime, ANALYSIS_PROMPT, args.api_url, args.model,
            args.timeout, args.max_tokens, args.temperature, args.retries, args.retry_delay,
        )
        observation = parse_observation(raw)
        return ObservationResult(item, observation, raw, tokens, "ok", elapsed=time.perf_counter() - started)
    except Exception as exc:
        return ObservationResult(item, {}, "", None, "error", str(exc), time.perf_counter() - started)


def report_row(result: ObservationResult) -> dict[str, str]:
    row = {field: "" for field in FIELDS}
    row.update(result.observation)
    row.update({
        "path": str(result.item.path), "kind": result.item.kind,
        "status": result.status, "tokens": "" if result.tokens is None else str(result.tokens),
        "elapsed_seconds": f"{result.elapsed:.3f}", "error": result.error or "",
        "raw_response": result.raw.replace("\t", " ").replace("\r", " ").replace("\n", " "),
    })
    return row


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    args = parse_args(argv or sys.argv[1:])
    input_root = Path(args.input).expanduser().resolve()
    if not input_root.is_dir():
        eprint(f"[err] input directory does not exist: {input_root}")
        return 2
    if args.workers < 1 or args.limit < 0 or args.offset < 0 or args.max_dim < 1 or args.video_seek < 0:
        eprint("[err] workers>=1, limit>=0, offset>=0, max-dim>=1, video-seek>=0 are required")
        return 2
    if args.include_videos and not is_ffmpeg_available():
        eprint("[err] --include-videos requires ffmpeg")
        return 2

    log_dir = input_root / "_logs"
    report = Path(args.report).expanduser().resolve() if args.report else log_dir / f"content_observation_report_{time.strftime('%Y%m%d_%H%M%S')}.tsv"
    excludes = {Path(value).expanduser().resolve() for value in args.exclude_dir}
    items = scan_media(input_root, args.recursive, args.include_videos, excludes)
    if args.offset:
        items = items[args.offset:]
    if args.limit > 0:
        items = items[:args.limit]

    print("=" * 72)
    print("LM Studio content discovery scanner")
    print(f"input:        {input_root}")
    print(f"report:       {report}")
    print(f"analysis:     {ANALYSIS_VERSION}; no predefined categories")
    print(f"model:        {args.model}")
    print(f"workers:      {args.workers}")
    print("action:       read-only; no files will be moved or copied")
    print(f"scanned:      {len(items)} (offset {args.offset})")
    print("=" * 72)

    done = errors = 0
    log_q: queue.Queue[str] = queue.Queue()
    stop_log = threading.Event()
    log_thread = threading.Thread(target=worker_log_consumer, args=(log_q, stop_log), daemon=True)
    log_thread.start()
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        fh.flush()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(analyze_item, item, args) for item in items]
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                result = future.result()
                done += 1
                errors += result.status != "ok"
                writer.writerow(report_row(result))
                fh.flush()
                label = private_item_label(index, len(items)) if args.private_logs else result.item.path.name
                log_q.put(f"[{index}/{len(items)}] {result.status}: {label} ({result.elapsed:.1f}s)")
    stop_log.set()
    log_thread.join(timeout=2)
    print("=" * 72)
    print(f"done:        {done}")
    print(f"ok:          {done - errors}")
    print(f"errors:      {errors}")
    print(f"report:      {report}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
