"""ComfyUI nodes for the local LM Studio media classifier."""

from .nodes.input_directory import InputDirectoryNode
from .nodes.output_directory import OutputDirectoryNode
from .nodes.preprocess import PreprocessNode
from .nodes.lmstudio import LMStudioNode
from .nodes.taxonomy import TaxonomyNode
from .nodes.cache import CacheNode
from .nodes.scan import ScanMediaNode
from .nodes.classify import ClassifyMediaNode
from .nodes.apply_output import ApplyOutputNode
from .nodes.route import RouteResultsNode
from .nodes.report import ReportNode


NODE_CLASS_MAPPINGS = {
    "MediaInputDirectory": InputDirectoryNode,
    "MediaOutputDirectory": OutputDirectoryNode,
    "MediaPreprocess": PreprocessNode,
    "MediaLMStudio": LMStudioNode,
    "MediaTaxonomy": TaxonomyNode,
    "MediaCache": CacheNode,
    "MediaScan": ScanMediaNode,
    "MediaClassify": ClassifyMediaNode,
    "MediaApplyOutput": ApplyOutputNode,
    "MediaRouteResults": RouteResultsNode,
    "MediaReport": ReportNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MediaInputDirectory": "媒体分类 / 输入目录",
    "MediaOutputDirectory": "媒体分类 / 输出目录",
    "MediaPreprocess": "媒体分类 / 预处理",
    "MediaLMStudio": "媒体分类 / LM Studio",
    "MediaTaxonomy": "媒体分类 / 分类要求",
    "MediaCache": "媒体分类 / 缓存",
    "MediaScan": "媒体分类 / 扫描媒体",
    "MediaClassify": "媒体分类 / 执行分类",
    "MediaApplyOutput": "媒体分类 / 文件输出",
    "MediaRouteResults": "媒体分类 / 结果分流",
    "MediaReport": "媒体分类 / 报告",
}
