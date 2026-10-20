from .. import classify_media_v8 as core


class TaxonomyNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "extra_requirements": ("STRING", {"default": "", "multiline": True}),
            }
        }

    RETURN_TYPES = ("MEDIA_TAXONOMY",)
    RETURN_NAMES = ("media_taxonomy",)
    FUNCTION = "execute"
    CATEGORY = "媒体分类"

    def execute(self, extra_requirements: str = ""):
        categories = dict(core.PROFILES["15"])
        prompt = core.build_prompt(categories)
        if extra_requirements.strip():
            prompt += "\n\nAdditional classification requirements:\n" + extra_requirements.strip()
        return ({"profile": "15", "categories": categories, "prompt": prompt},)
