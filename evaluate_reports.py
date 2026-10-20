#!/usr/bin/env python3
"""Compare classifier TSV reports with a manually labeled TSV."""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from pathlib import Path


def path_key(value: str) -> str:
    value = value.strip()
    return os.path.normcase(os.path.normpath(value)) if value else ""


def read_tsv(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")
        return list(reader)


def load_labels(path: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    for row in read_tsv(path, {"path", "category"}):
        key, category = path_key(row["path"] or ""), (row["category"] or "").strip()
        if not key or not category:
            raise ValueError(f"{path}: every label row needs path and category")
        if key in labels and labels[key] != category:
            raise ValueError(f"{path}: conflicting labels for {row['path']}")
        labels[key] = category
    if not labels:
        raise ValueError(f"{path}: no labels found")
    return labels


def is_nsfw(category: str) -> bool:
    return "nsfw" in category.casefold()


def score(labels: dict[str, str], report_path: Path, top: int) -> None:
    predictions: dict[str, str] = {}
    for row in read_tsv(report_path, {"path", "status", "category"}):
        key = path_key(row["path"] or "")
        if key:
            status = (row["status"] or "").strip().casefold()
            predictions[key] = (row["category"] or "").strip() if status == "ok" else ""

    pairs = [(actual, predictions.get(key, "")) for key, actual in labels.items()]
    missing = sum(not predicted for _, predicted in pairs)
    correct = sum(actual == predicted for actual, predicted in pairs)
    classes = {actual for actual, _ in pairs} | {predicted for _, predicted in pairs if predicted}
    f1s = []
    for category in classes:
        tp = sum(actual == category and predicted == category for actual, predicted in pairs)
        fp = sum(actual != category and predicted == category for actual, predicted in pairs)
        fn = sum(actual == category and predicted != category for actual, predicted in pairs)
        f1s.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0)
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0
    nsfw_fns = sum(
        is_nsfw(actual) and not is_nsfw(predictions.get(key, ""))
        for key, actual in labels.items()
    )
    confusions = Counter((actual, predicted) for actual, predicted in pairs if predicted and actual != predicted)

    print(f"\n{report_path}")
    print(f"labeled: {len(labels)}  matched: {len(labels) - missing}  missing predictions: {missing}")
    print(f"accuracy: {correct / len(labels):.1%} ({correct}/{len(labels)} labeled)")
    print(f"macro F1: {macro_f1:.1%}  NSFW false negatives: {nsfw_fns}")
    print("highest-count confusions:")
    for (actual, predicted), count in confusions.most_common(top):
        print(f"  {actual} -> {predicted}: {count}")
    if not confusions:
        print("  none")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path, help="TSV with path and category columns")
    parser.add_argument("reports", type=Path, nargs="+", help="one or more classifier TSV reports")
    parser.add_argument("--top", type=int, default=10, help="number of confusions to show (default: 10)")
    args = parser.parse_args()
    if args.top < 1:
        parser.error("--top must be at least 1")
    try:
        labels = load_labels(args.labels)
        for report in args.reports:
            score(labels, report, args.top)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
