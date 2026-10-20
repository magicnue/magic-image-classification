from .. import classify_media_v8 as core


class LMStudioNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "api_url": ("STRING", {"default": core.DEFAULT_API_URL, "multiline": False}),
                "model": ("STRING", {"default": core.DEFAULT_MODEL, "multiline": False}),
                "workers": ("INT", {"default": 1, "min": 1, "max": 256}),
                "timeout": ("INT", {"default": 180, "min": 1, "max": 86400}),
                "max_tokens": ("INT", {"default": core.DEFAULT_MAX_TOKENS, "min": 1, "max": 100000}),
                "temperature": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 2.0, "step": 0.01}),
                "retries": ("INT", {"default": core.DEFAULT_RETRIES, "min": 0, "max": 100}),
                "retry_delay": ("FLOAT", {"default": core.DEFAULT_RETRY_DELAY, "min": 0.0, "max": 3600.0, "step": 0.1}),
                "lms_context": ("INT", {"default": 8192, "min": 1, "max": 1048576}),
                "lms_model_key": ("STRING", {"default": core.DEFAULT_LMS_MODEL_KEY, "multiline": False}),
            }
        }

    RETURN_TYPES = ("MEDIA_LMSTUDIO",)
    RETURN_NAMES = ("media_lmstudio",)
    FUNCTION = "execute"
    CATEGORY = "媒体分类"

    def execute(
        self,
        api_url: str,
        model: str,
        workers: int,
        timeout: int,
        max_tokens: int,
        temperature: float,
        retries: int,
        retry_delay: float,
        lms_context: int,
        lms_model_key: str,
    ):
        return ({
            "api_url": api_url,
            "model": model,
            "workers": workers,
            "timeout": timeout,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "retries": retries,
            "retry_delay": retry_delay,
            "lms_context": lms_context,
            "lms_model_key": lms_model_key,
        },)
