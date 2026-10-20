from pathlib import Path


class InputDirectoryNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "root": ("STRING", {"default": "", "multiline": False}),
                "recursive": ("BOOLEAN", {"default": False}),
                "include_videos": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("MEDIA_INPUT",)
    RETURN_NAMES = ("media_input",)
    FUNCTION = "execute"
    CATEGORY = "媒体分类"

    def execute(self, root: str, recursive: bool, include_videos: bool):
        value = root.strip()
        if not value:
            raise ValueError("Input directory cannot be empty")
        path = Path(value).expanduser()
        if not path.is_dir():
            raise ValueError(f"Input directory does not exist: {path}")
        return ({"root": path, "recursive": recursive, "include_videos": include_videos},)
