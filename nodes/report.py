"""Write a TSV report for a classification run."""

from pathlib import Path

from .. import classify_media_v8 as core


class ReportNode:
    CATEGORY = "媒体分类"
    FUNCTION = "write_report"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("report_path", "summary")
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "results": ("MEDIA_RESULTS",),
            "report_path": ("STRING", {"default": ""}),
        }}

    def write_report(self, results, report_path):
        path = Path(report_path).expanduser().resolve() if report_path.strip() else (
            results["output_root"] / "_logs" /
            f"classification_report_v8_{core.make_report_timestamp()}.tsv"
        )
        rows = results["results"]
        core.export_report(rows, path)
        ok = sum(r.status == "ok" for r in rows)
        review = sum(r.status == "review" for r in rows)
        summary = f"total={len(rows)} ok={ok} review={review} error={len(rows) - ok - review}"
        return (str(path), summary)
