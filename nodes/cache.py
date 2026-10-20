class CacheNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "db_path": ("STRING", {"default": "", "multiline": False}),
                "force": ("BOOLEAN", {"default": False}),
                "limit": ("INT", {"default": 0, "min": 0, "max": 1000000000}),
            }
        }

    RETURN_TYPES = ("MEDIA_CACHE",)
    RETURN_NAMES = ("media_cache",)
    FUNCTION = "execute"
    CATEGORY = "媒体分类"

    def execute(self, db_path: str, force: bool, limit: int):
        return ({"db_path": db_path.strip(), "force": force, "limit": limit},)
