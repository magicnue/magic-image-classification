"""Apply the selected file action to classified media."""

from dataclasses import replace
from pathlib import Path

from .. import classify_media_v8 as core


class ApplyOutputNode:
    CATEGORY = "媒体分类"
    FUNCTION = "apply"
    RETURN_TYPES = ("MEDIA_RESULTS",)
    RETURN_NAMES = ("results",)
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"results": ("MEDIA_RESULTS",), "output_config": ("MEDIA_OUTPUT",)}}

    def apply(self, results, output_config):
        output_root = Path(output_config["root"]).expanduser().resolve() if output_config["root"] else results["input_root"]
        destinations = []
        updated_results = []
        for result in results["results"]:
            updated = replace(result)
            try:
                dest = core.apply_file_action(
                    updated,
                    results["input_root"],
                    output_root,
                    output_config["action"],
                    output_config["preserve_tree"],
                    output_config["review_dir"],
                )
                destinations.append(str(dest) if dest else "")
            except OSError as exc:
                updated.status = "error"
                updated.error = str(exc)
                destinations.append("")
            updated_results.append(updated)
        return ({**results, "results": updated_results, "output_root": output_root, "destinations": destinations},)
