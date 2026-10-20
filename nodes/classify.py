import argparse
import concurrent.futures
from pathlib import Path

from .. import classify_media_v8 as core


class ClassifyMediaNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "media": ("MEDIA_ITEMS",),
                "preprocess": ("MEDIA_PREPROCESS",),
                "lmstudio": ("MEDIA_LMSTUDIO",),
                "taxonomy": ("MEDIA_TAXONOMY",),
                "cache": ("MEDIA_CACHE",),
                "output_config": ("MEDIA_OUTPUT",),
            }
        }

    RETURN_TYPES = ("MEDIA_RESULTS",)
    RETURN_NAMES = ("results",)
    FUNCTION = "classify"
    CATEGORY = "媒体分类"

    def classify(self, media, preprocess, lmstudio, taxonomy, cache, output_config):
        items = media["items"]
        input_root = Path(media["input_root"])
        output_root = Path(output_config["root"]).expanduser().resolve() if output_config["root"] else input_root
        categories = taxonomy["categories"]
        prompt = taxonomy["prompt"]
        api_url = lmstudio["api_url"]
        model = lmstudio["model"]
        profile = taxonomy["profile"]

        profile_args = argparse.Namespace(
            profile=profile,
            api_url=api_url,
            model=model,
            max_tokens=lmstudio["max_tokens"],
            temperature=lmstudio["temperature"],
            max_dim=preprocess["max_dim"],
            no_resize=preprocess["no_resize"],
            video_frames=preprocess["video_frames"],
            video_seek=preprocess["video_seek"],
        )
        cache_profile = core.make_cache_profile(profile_args, prompt)
        db_path = (
            Path(cache["db_path"]).expanduser().resolve()
            if cache["db_path"]
            else output_root / "_logs" / ".classification_state_v8.sqlite3"
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = core.ProgressStore(db_path)
        results = []

        try:
            pending = []
            for item in items:
                cached = None if cache["force"] else store.get_done(item, cache_profile, model)
                if cached:
                    results.append(
                        core.ClassificationResult(
                            item=item,
                            category=cached["category"],
                            dims=cached["dims"],
                            raw="[cached]",
                            tokens=None,
                            status=cached["status"],
                            contradiction=cached["contradiction"],
                        )
                    )
                else:
                    pending.append(item)

            if cache["limit"] > 0:
                pending = pending[: cache["limit"]]

            if pending:
                core.ensure_lms_server(api_url)
                core.maybe_load_lms_model(
                    model,
                    lmstudio["lms_model_key"],
                    lmstudio["lms_context"],
                    api_url,
                )
                core.preflight_api(api_url, lmstudio["timeout"])
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=max(1, lmstudio["workers"])
                ) as executor:
                    future_map = {
                        executor.submit(
                            core.classify_item,
                            item,
                            prompt,
                            categories,
                            api_url,
                            model,
                            lmstudio["timeout"],
                            lmstudio["max_tokens"],
                            preprocess["max_dim"],
                            preprocess["no_resize"],
                            preprocess["video_seek"],
                            lmstudio["temperature"],
                            lmstudio["retries"],
                            lmstudio["retry_delay"],
                            preprocess["video_frames"],
                        ): item
                        for item in pending
                    }
                    for future in concurrent.futures.as_completed(future_map):
                        result = future.result()
                        store.save(result, cache_profile, model)
                        results.append(result)

            return ({"results": results, "input_root": input_root, "output_root": output_root},)
        finally:
            store.close()
