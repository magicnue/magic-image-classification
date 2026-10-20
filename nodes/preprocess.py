class PreprocessNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "max_dim": ("INT", {"default": 1024, "min": 1, "max": 16384}),
                "no_resize": ("BOOLEAN", {"default": False}),
                "video_seek": ("FLOAT", {"default": 3.0, "min": 0.0, "max": 86400.0, "step": 0.1}),
                "video_frames": ("INT", {"default": 1, "min": 1, "max": 100}),
            }
        }

    RETURN_TYPES = ("MEDIA_PREPROCESS",)
    RETURN_NAMES = ("media_preprocess",)
    FUNCTION = "execute"
    CATEGORY = "媒体分类"

    def execute(self, max_dim: int, no_resize: bool, video_seek: float, video_frames: int):
        return ({
            "max_dim": max_dim,
            "no_resize": no_resize,
            "video_seek": video_seek,
            "video_frames": video_frames,
        },)
