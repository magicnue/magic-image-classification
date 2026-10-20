# 图像分类项目

使用 LM Studio 中的本地视觉模型识别图片和视频，并按类别整理文件。当前主入口为 `classify_media_v8.py`，使用 LM Studio 原生 `/api/v1/chat` 接口和 `reasoning: off`。

## 运行要求

- Python 3：主分类、内容扫描和报告比较脚本仅使用标准库。
- LM Studio：运行本地 API 服务并加载视觉模型。默认接口为 `http://127.0.0.1:1234/api/v1/chat`。
- ffmpeg：用于图像预处理和视频抽帧；ffprobe 用于媒体尺寸、时长和多帧处理。
- Pillow：仅 `review_small_square_images.py` 需要。

默认模型标识为 `qwen3.5-9b-abliterated-vision-q4_k_m`，对应 LM Studio 模型键为 `qwen3.5-9b-abliterated`。使用其他模型时，通过 `--model` 和 `--lms-model-key` 指定。

## 命令行使用

在项目目录执行。示例中的 `./input`、`./output` 均可替换为实际目录。

```powershell
# 抽样识别，只保存报告，不移动或复制媒体
python ./classify_media_v8.py --input ./input --output ./output --recursive --action dry-run --limit 20 --private-logs

# 确认结果后，再移动文件到分类目录
python ./classify_media_v8.py --input ./input --output ./output --recursive --action move --private-logs

# 包含视频，取第 0.5 秒的一帧
python ./classify_media_v8.py --input ./input --output ./output --recursive --include-videos --video-seek 0.5 --action dry-run --limit 20 --private-logs
```

脚本默认动作为 `move`，建议始终显式传入 `--action`。`dry-run` 计算分类结果和目标路径；`copy` 复制源文件；`move` 移动源文件。默认输出目录与输入目录相同，示例显式指定 `./output`。

常用参数：

- `--recursive`：扫描子目录。
- `--workers`：并发请求数，默认 `1`；实际推理并发还取决于 LM Studio 的模型加载配置。
- `--limit`：最多处理多少个待分类文件；仍会先扫描输入目录。
- `--include-videos`：包含视频，默认单帧；`--video-frames 3` 可启用多帧。
- `--max-dim`：发送给模型的图像最长边，默认 `1024`。
- `--logs-dir`：指定报告和状态库目录，默认 `<output>/_logs`。
- `--force`：忽略已完成结果的缓存。
- `--api-url`、`--model`：指定接口和模型。

完整参数通过 `python ./classify_media_v8.py --help` 查看。

## 分类和报告

主分类包括：二次元_NSFW、三次元_NSFW、隐私文档、游戏截图、二次元_梗图、影视综艺截图、资讯文档截图、军事装备、动物萌宠、二次元_插画、绘画艺术、CG渲染、三次元_SFW、生活记录、无主体。

每条结果还记录 `nsfw`、`medium`、`animal`、`people`、`text` 维度。`animal=yes` 与“动物萌宠”主分类可用于不同范围的筛选。

分类结果逐条写入 SQLite，结束后导出 TSV 报告；修改模型、提示词或预处理参数会改变缓存配置。矛盾结果标记为 `review`，交由人工复核。报告中的 `ok` 表示通过解析和一致性检查，不能代替准确率评估。

## ComfyUI 节点

将整个项目放入 `ComfyUI/custom_nodes/`，重启 ComfyUI，在“媒体分类”分类下添加节点。根目录的 `classify_media_v8.py` 需要保留，节点复用它的分类逻辑。

可导入工作流：`workflows/media_classification_v8.json`。输入和输出使用相对路径 `./input`、`./output`，相对于启动 ComfyUI 时的工作目录；导入后应在“输入目录”“输出目录”节点中设置实际位置。默认文件动作为 `dry-run`，每批最多新分类 20 个文件。

连接顺序：

1. “输入目录”“输出目录”“分类要求”接到“扫描媒体”。
2. “扫描媒体”的 `media` 与“预处理”“LM Studio”“分类要求”“缓存”“输出目录”接到“执行分类”。
3. “执行分类”的 `results` 和“输出目录”接到“文件输出”。
4. “文件输出”的 `results` 接到“结果分流”，再将 `all_results` 接到“报告”。

扫描节点每次排队都会重扫目录；执行分类节点按 `workers` 请求 LM Studio 并记录缓存。默认视频单帧处理，报告输出到所选输出目录的 `_logs/`。

## 其他工具

- `scan_media_v4.py`：输出主体、场景、媒介形式等内容观察信息，不移动或复制文件。
- `evaluate_reports.py`：比较人工标签和分类报告，计算准确率、宏 F1 和混淆情况。
- `review_small_square_images.py`：收集小尺寸方形图供人工审核，并支持将保留的候选还原。`collect --dry-run` 仅统计；普通 `collect` 会移动候选文件。

```powershell
python ./scan_media_v4.py --input ./input --recursive --limit 20 --private-logs
python ./evaluate_reports.py labels.tsv report.tsv
python ./review_small_square_images.py collect --input ./input --dry-run
```

## 本地资料

个人路径、私有工作流、使用记录和旧版本资料统一放在 `local/`，该目录由 `.gitignore` 排除：

```text
local/
  archives/       # 旧脚本、旧压缩包和早期记录
  notes/          # 本机使用记录
  workflows/      # 含本机路径的工作流
```

媒体输入、输出、报告和数据库也已通过 `.gitignore` 排除。新增个人配置放入 `local/`，需要共享的模板使用通用路径。忽略规则只影响后续跟踪，不会清除已有 Git 提交中的内容。
