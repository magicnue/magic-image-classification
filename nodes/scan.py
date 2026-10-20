from pathlib import Path

from .. import classify_media_v8 as core


class ScanMediaNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "input_config": ("MEDIA_INPUT",),
                "output_config": ("MEDIA_OUTPUT",),
                "taxonomy": ("MEDIA_TAXONOMY",),
            }
        }

    RETURN_TYPES = ("MEDIA_ITEMS",)
    RETURN_NAMES = ("media",)
    FUNCTION = "scan"
    CATEGORY = "媒体分类"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def scan(self, input_config, output_config, taxonomy):
        input_root = Path(input_config["root"]).expanduser().resolve()
        output_root = Path(output_config["root"]).expanduser().resolve() if output_config["root"] else input_root

        if output_root != input_root:
            excludes = {output_root}
        else:
            excludes = {
                child
                for child in output_root.iterdir()
                if child.is_dir()
                and core.is_category_output_dir(
                    child.name,
                    taxonomy["categories"],
                    output_config["review_dir"],
                )
            }
        excludes.add(output_root / "_codex_video_frames")
        items = core.scan_media(
            input_root,
            input_config["recursive"],
            input_config["include_videos"],
            excludes,
        )
        return ({"items": items, "input_root": input_root},)
