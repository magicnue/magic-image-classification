"""Split classification results by status."""


class RouteResultsNode:
    CATEGORY = "媒体分类"
    FUNCTION = "route"
    RETURN_TYPES = ("MEDIA_RESULTS", "MEDIA_RESULTS", "MEDIA_RESULTS", "MEDIA_RESULTS")
    RETURN_NAMES = ("ok", "review", "error", "all_results")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"results": ("MEDIA_RESULTS",)}}

    def route(self, results):
        rows = results["results"]
        return (
            {**results, "results": [r for r in rows if r.status == "ok"]},
            {**results, "results": [r for r in rows if r.status == "review"]},
            {**results, "results": [r for r in rows if r.status not in ("ok", "review")]},
            results,
        )
