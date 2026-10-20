class OutputDirectoryNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "root": ("STRING", {"default": "", "multiline": False}),
                "action": (["move", "copy", "dry-run"], {"default": "move"}),
                "preserve_tree": ("BOOLEAN", {"default": False}),
                "review_dir": ("STRING", {"default": "未分类", "multiline": False}),
            }
        }

    RETURN_TYPES = ("MEDIA_OUTPUT",)
    RETURN_NAMES = ("media_output",)
    FUNCTION = "execute"
    CATEGORY = "媒体分类"

    def execute(self, root: str, action: str, preserve_tree: bool, review_dir: str):
        return ({
            "root": root.strip(),
            "action": action,
            "preserve_tree": preserve_tree,
            "review_dir": review_dir,
        },)
