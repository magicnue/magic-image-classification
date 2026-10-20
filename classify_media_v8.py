#!/usr/bin/env python3
"""Standalone LM Studio media classifier v8."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import dataclasses
import hashlib
import json
import mimetypes
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_API_URL = "http://127.0.0.1:1234/api/v1/chat"
DEFAULT_MODEL = "qwen3.5-9b-abliterated-vision-q4_k_m"
DEFAULT_LMS_MODEL_KEY = "qwen3.5-9b-abliterated"
DEFAULT_MAX_DIM = 1024
DEFAULT_MAX_TOKENS = 96
DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY = 2.0
DEFAULT_REVIEW_DIR = "未分类"
LONG_IMAGE_RATIO = 3.0

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
SKIP_NAMES = {".ds_store", "thumbs.db", "desktop.ini", ".nomedia"}


PROFILES: dict[str, dict[str, str]] = {
    "15": {
        # Order is priority: first matching category wins.
        "二次元_NSFW": "2D drawn anime/manga/CG content with nudity, explicit sexual content, strong sexualized framing, or graphic gore/body horror. Includes sexual galgame/eroge/visual-novel dialogue screenshots, sexual 2D meme images, and lewd anime figure/merchandise photos. Do NOT classify these as game screenshots or meme images.",
        "三次元_NSFW": "real-world photo or live-action frame with nudity, explicit sexual content, strong sexualized cosplay/erotic posing, or graphic gore/severe injury/body horror. Includes borderline/ecchi cosplay photos and erotic photos of real people.",
        "隐私文档": "photo or scan of privacy-sensitive personal documents: ID card, bank card, passport, driver license, medical record, contract, or any document clearly containing personal sensitive information.",
        "游戏截图": "video-game screenshot or in-game UI: gameplay, HUD, crosshair, health bar, minimap, skill bar, inventory, team list, game chat, facecam overlay, Discord/QQ voice overlay, game login screen, in-game menu, or game event announcement page. Excludes galgame/eroge/visual-novel dialogue screenshots.",
        "二次元_梗图": "2D anime-style meme or reaction image with visible overlaid caption text, added speech bubble, repost/forward watermark, obvious sticker/reaction framing, or abnormal cropped close-up expression. Multi-sticker sheet/collection images also count as meme images.",
        "二次元_插画": "2D work with clear Japanese anime, manga, visual-novel, or anime-game visual language. Anime identity overrides technique: watercolor, oil paint, impasto, sketch, pixel art, ink, or digital painting remains 二次元_插画 when anime-style. No character is required; anime backgrounds, scenery, environment art, and location CG belong here. Also includes anime-looking AI art, photographed manga, and anime-focused figure/merchandise/itasha photos.",
        "影视综艺截图": "movie, TV drama, anime episode, live show, or variety-show screenshot with subtitles, broadcast captions, station logo, on-screen variety text, danmaku overlay, or obvious cinematic/episode framing.",
        "资讯文档截图": "text/UI-dominated screenshot or photographed screen/page: news webpage, chat log, social media post, forum, announcement, document, form, table, spreadsheet, software UI, receipt, scanned paper. Also includes: maps, blueprints, medical/scientific imaging, QR codes, payment/membership codes, and music/app share cards with UI frame.",
        "军事装备": "weapon, firearm, aircraft, tank, warship, armored vehicle, missile, soldier equipment, or other military hardware as the main visible subject. Do not use for a game screenshot that merely contains military objects.",
        "动物萌宠": "animal as the main visible subject: cat, dog, bird, pet, wildlife, or animal close-up. Real animals or drawn animals are both allowed when the animal is the focus and no higher-priority class applies.",
        "绘画艺术": "2D art without clear Japanese anime/manga visual language: realistic or traditional oil painting, watercolor, sketch, Chinese painting, ukiyo-e, calligraphy, children's drawing, western comics, or non-anime concept art. Technique alone never overrides anime visual identity.",
        "CG渲染": "non-anime 3D modeling/rendering: architectural visualization, product render, realistic 3D people, western CG animation (Pixar-like), or other non-anime 3D. Anime-style 3D follows the anime content categories.",
        "三次元_SFW": "real-world safe-for-work photo or live-action frame with people, portraits, cosplay, landscape, street, event, travel, concert, daily life, or ordinary scene without nudity/sexual content. Safe cosplay photos of real people belong here.",
        "生活记录": "safe everyday REAL-WORLD PHOTO: food, product, object, appliance, package, travel detail, shop, room, desk, or miscellaneous daily record. Requires medium=photo; drawn/rendered everyday scenes are classified by visual style.",
        "无主体": "image with no recognizable subject: solid color, gradient, abstract pattern, wallpaper material, design texture, corrupted or tiny thumbnail, blank image.",
    },
}

NSFW_CATEGORIES = {"二次元_NSFW", "三次元_NSFW"}
PHOTO_ONLY_CATEGORIES = {"三次元_NSFW", "三次元_SFW", "生活记录"}
TEXT_DOMINANT_CATEGORY = "资讯文档截图"

DIMENSION_KEYS = ("nsfw", "medium", "animal", "people", "text")


@dataclasses.dataclass(frozen=True)
class MediaItem:
    path: Path
    kind: str
    size: int
    mtime_ns: int
    fingerprint: str


@dataclasses.dataclass
class ClassificationResult:
    item: MediaItem
    category: str | None
    dims: dict[str, str]
    raw: str
    tokens: int | None
    status: str
    contradiction: str | None = None
    error: str | None = None
    elapsed: float = 0.0


class ProgressStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                fingerprint TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                kind TEXT NOT NULL,
                profile TEXT NOT NULL,
                model TEXT NOT NULL,
                category TEXT,
                nsfw TEXT,
                medium TEXT,
                animal TEXT,
                people TEXT,
                text_heavy TEXT,
                contradiction TEXT,
                raw_response TEXT,
                tokens INTEGER,
                status TEXT NOT NULL,
                error TEXT,
                updated_at REAL NOT NULL
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get_done(self, item: MediaItem, profile: str, model: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT category, status, nsfw, medium, animal, people, text_heavy, contradiction
                FROM results
                WHERE fingerprint=? AND size=? AND mtime_ns=? AND profile=? AND model=?
                """,
                (item.fingerprint, item.size, item.mtime_ns, profile, model),
            ).fetchone()
        if not row:
            return None
        category, status, nsfw, medium, animal, people, text_heavy, contradiction = row
        if status not in ("ok", "review") or not category:
            return None
        return {
            "category": str(category),
            "status": str(status),
            "dims": {
                "nsfw": nsfw or "",
                "medium": medium or "",
                "animal": animal or "",
                "people": people or "",
                "text": text_heavy or "",
            },
            "contradiction": contradiction,
        }

    def save(self, result: ClassificationResult, profile: str, model: str) -> None:
        item = result.item
        dims = result.dims
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO results (
                    fingerprint, source_path, size, mtime_ns, kind, profile, model,
                    category, nsfw, medium, animal, people, text_heavy, contradiction,
                    raw_response, tokens, status, error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    source_path=excluded.source_path,
                    size=excluded.size,
                    mtime_ns=excluded.mtime_ns,
                    kind=excluded.kind,
                    profile=excluded.profile,
                    model=excluded.model,
                    category=excluded.category,
                    nsfw=excluded.nsfw,
                    medium=excluded.medium,
                    animal=excluded.animal,
                    people=excluded.people,
                    text_heavy=excluded.text_heavy,
                    contradiction=excluded.contradiction,
                    raw_response=excluded.raw_response,
                    tokens=excluded.tokens,
                    status=excluded.status,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (
                    item.fingerprint,
                    str(item.path),
                    item.size,
                    item.mtime_ns,
                    item.kind,
                    profile,
                    model,
                    result.category,
                    dims.get("nsfw"),
                    dims.get("medium"),
                    dims.get("animal"),
                    dims.get("people"),
                    dims.get("text"),
                    result.contradiction,
                    result.raw,
                    result.tokens,
                    result.status,
                    result.error,
                    time.time(),
                ),
            )
            self.conn.commit()


def eprint(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def is_ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def stable_fingerprint(path: Path, size: int, mtime_ns: int) -> str:
    h = hashlib.sha256()
    h.update(str(path.resolve()).encode("utf-8", errors="surrogatepass"))
    h.update(str(size).encode("ascii"))
    h.update(str(mtime_ns).encode("ascii"))
    return h.hexdigest()


def scan_media(root: Path, recursive: bool, include_videos: bool, exclude_dirs: set[Path]) -> list[MediaItem]:
    pattern = "**/*" if recursive else "*"
    items: list[MediaItem] = []
    resolved_excludes = {p.resolve() for p in exclude_dirs}
    for path in root.glob(pattern):
        if not path.is_file():
            continue
        if path.name.lower() in SKIP_NAMES:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if any(excluded == resolved or excluded in resolved.parents for excluded in resolved_excludes):
            continue
        ext = path.suffix.lower()
        kind = ""
        if ext in IMAGE_EXTS:
            kind = "image"
        elif include_videos and ext in VIDEO_EXTS:
            kind = "video"
        else:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size == 0:
            continue
        items.append(
            MediaItem(
                path=path,
                kind=kind,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                fingerprint=stable_fingerprint(path, stat.st_size, stat.st_mtime_ns),
            )
        )
    items.sort(key=lambda x: str(x.path).lower())
    return items


def build_prompt(categories: dict[str, str]) -> str:
    lines = [
        "Classify this media and answer with a SINGLE JSON object. No other text, no code fence.",
        "Use only directly visible visual evidence. Do NOT infer intent, author, source website, or hidden context.",
        "The image may be one half of a very tall long image; classify based on what is visible in it.",
        "",
        "Step 1 - choose EXACTLY ONE main category. Priority rules:",
        "1. NSFW overrides everything when nudity, explicit sexual content, or graphic gore/body horror is visible. If large mosaic/blur censors the content but composition still suggests explicit material, lean NSFW (isolation over miss).",
        "1b. Strict edge rule: when in doubt, lean NSFW. Set nsfw=yes and choose the NSFW category if the image focuses on swimwear/lingerie/glossy or tight body-hugging clothing on a body, uses sexually suggestive posing or camera angle, or any visible text/dialogue/caption carries sexual innuendo. Meme-style framing does NOT exempt such images from the NSFW categories.",
        "2. Determine 2D vs real-world for NSFW by the SUBJECT nature: fictional drawn/figure character goes to 二次元_NSFW, real person/live-action goes to 三次元_NSFW. Borderline/ecchi cosplay photos are 三次元_NSFW.",
        "3. Privacy-sensitive personal documents (ID card, bank card, passport, medical record, contract) are 隐私文档.",
        "4. Galgame, eroge, and visual-novel dialogue screenshots are NOT game screenshots; classify sexual ones as 二次元_NSFW.",
        "5. Gameplay or in-game UI (HUD, minimap, login screen, in-game menu, event page) is 游戏截图, even if captioned.",
        "6. Anime meme/reaction/sticker evidence goes to 二次元_梗图; multi-sticker sheets count too.",
        "7. If an image is text/UI dominated, use 资讯文档截图 unless a higher-priority class applies. Maps, blueprints, medical/scientific imaging, QR/payment codes, and music/app share cards with UI frame also go here.",
        "8. If uncertain between meme and illustration, choose 二次元_插画 unless visible meme evidence exists.",
        "9. Photos of anime figures/merchandise/itasha cars: if the anime character is the MAIN visual focus, classify as 2D (二次元_NSFW or 二次元_插画 by content); anime elements only in unnoticeable edges do not count.",
        "10. AI-generated images are classified by content style: anime look goes to 2D categories.",
        "11. Photographed manga/comic pages follow rule 9: drawn content as main focus goes to 二次元 categories.",
        "12. Collages/split images: judge by the LARGEST part; if parts are equal, text-bearing parts win.",
        "13. Anime visual language overrides technique; anime watercolor/oil/sketch/pixel/ink work remains 二次元_插画.",
        "14. A person is not required: anime backgrounds and environment art remain 二次元_插画.",
        "15. 生活记录 requires medium=photo; classify drawn/rendered daily scenes by visual style.",
        "16. CG渲染 is non-anime 3D; anime-style 3D follows anime categories.",
        "",
        "Step 2 - fill every dimension with its allowed values:",
        '- "nsfw": "yes" if nudity, explicit sex, graphic gore/body horror is visible, OR rule 1b applies (sexualized clothing focus, suggestive posing, sexual innuendo text); otherwise "no".',
        '- "medium": "drawn" if the content is drawn/painted/CG/rendered/anime figure merchandise. "photo" if the subject is real-world (real people, real scenes). Judge by SUBJECT nature, not camera usage: a real cosplayer is "photo"; an anime figure photo is "drawn". AI images follow their visual style.',
        '- "animal": "yes" if an animal (cat, dog, bird, wildlife, drawn animal) is clearly visible as a main subject, otherwise "no".',
        '- "people": "yes" if a person or humanoid character with visible face or body is present, otherwise "no".',
        '- "text": "yes" if overlaid text, subtitles, captions, chat, or UI dominates the image, otherwise "no".',
        "",
        "Consistency rules you MUST obey:",
        "- main is 二次元_NSFW or 三次元_NSFW => nsfw must be \"yes\". All other main categories => nsfw must be \"no\".",
        '- main in {三次元_NSFW, 三次元_SFW} => medium must be "photo".',
        '- main is 生活记录 => medium must be "photo".',
        "",
        "Main categories:",
    ]
    for name, desc in categories.items():
        lines.append(f"- {name}: {desc}")
    lines.append("")
    lines.append('JSON keys: "main", "nsfw", "medium", "animal", "people", "text".')
    lines.append('"main" must be exactly one of: ' + ", ".join(categories.keys()))
    lines.append("")
    lines.append('Example: {"main":"二次元_插画","nsfw":"no","medium":"drawn","animal":"no","people":"yes","text":"no"}')
    lines.append("")
    lines.append("Return ONLY the JSON object.")
    return "\n".join(lines)


def guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("image/"):
        return mime
    if path.suffix.lower() == ".bmp":
        return "image/bmp"
    return "image/jpeg"


def run_ffmpeg(args: list[str], timeout: int) -> bytes:
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err[-500:] or f"ffmpeg exited with {result.returncode}")
    return result.stdout


def probe_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path),
            ],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None


def probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


def _frame_pipe_args(max_dim: int) -> list[str]:
    return [
        "-vf", f"scale={max_dim}:{max_dim}:force_original_aspect_ratio=decrease",
        "-frames:v", "1", "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
    ]


def image_to_jpeg_bytes(path: Path, max_dim: int, no_resize: bool) -> tuple[bytes, str]:
    if not no_resize and is_ffmpeg_available():
        try:
            data = run_ffmpeg(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path)]
                + _frame_pipe_args(max_dim),
                timeout=45,
            )
            if data:
                return data, "image/jpeg"
        except Exception as exc:
            eprint(f"[warn] ffmpeg image resize failed, using original: {path.name}: {exc}")
    return path.read_bytes(), guess_mime(path)


def gif_middle_frame(path: Path, max_dim: int) -> bytes | None:
    """Extract the middle frame of a GIF; first frames are often blank."""
    if not (is_ffmpeg_available() and is_ffprobe_available()):
        return None
    duration = probe_duration(path)
    if not duration or duration <= 0:
        return None
    seek = max(0.0, duration / 2 - 0.05)
    try:
        data = run_ffmpeg(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{seek:.3f}", "-i", str(path)]
            + _frame_pipe_args(max_dim),
            timeout=45,
        )
        return data or None
    except Exception:
        return None


def long_image_halves(path: Path, max_dim: int) -> list[bytes]:
    """Split a very tall image into top/bottom halves for separate classification."""
    if not (is_ffmpeg_available() and is_ffprobe_available()):
        return []
    dims = probe_dimensions(path)
    if not dims:
        return []
    width, height = dims
    if width <= 0 or height / width <= LONG_IMAGE_RATIO:
        return []
    halves: list[bytes] = []
    for offset in ("0", "ih/2"):
        try:
            vf = f"crop=iw:ih/2:0:{offset},scale={max_dim}:{max_dim}:force_original_aspect_ratio=decrease"
            data = run_ffmpeg(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                    "-vf", vf, "-frames:v", "1", "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
                ],
                timeout=60,
            )
            if data:
                halves.append(data)
        except Exception:
            continue
    return halves if len(halves) == 2 else []


def video_frame_to_jpeg_bytes(path: Path, seek: float, max_dim: int) -> tuple[bytes, str]:
    if not is_ffmpeg_available():
        raise RuntimeError("ffmpeg is required for video classification")
    data = run_ffmpeg(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{seek:.3f}", "-i", str(path)]
        + _frame_pipe_args(max_dim),
        timeout=90,
    )
    if not data:
        raise RuntimeError("ffmpeg produced no frame")
    return data, "image/jpeg"


def build_payloads(item: MediaItem, max_dim: int, no_resize: bool, video_seek: float, video_frames: int = 1) -> list[tuple[bytes, str]]:
    """Return one or more (image_bytes, mime) payloads to classify for this item."""
    if item.kind == "video":
        if video_frames <= 1:
            return [video_frame_to_jpeg_bytes(item.path, video_seek, max_dim)]
        duration = probe_duration(item.path)
        if not duration or duration <= 0:
            raise RuntimeError("ffprobe is required for multi-frame video classification")
        positions = [duration * (index + 1) / (video_frames + 1) for index in range(video_frames)]
        return [video_frame_to_jpeg_bytes(item.path, position, max_dim) for position in positions]
    if item.path.suffix.lower() == ".gif" and not no_resize:
        middle = gif_middle_frame(item.path, max_dim)
        if middle:
            return [(middle, "image/jpeg")]
    if not no_resize:
        halves = long_image_halves(item.path, max_dim)
        if halves:
            return [(h, "image/jpeg") for h in halves]
    return [image_to_jpeg_bytes(item.path, max_dim, no_resize)]

def build_models_url(api_url: str) -> str:
    parsed = urllib.parse.urlsplit(api_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/api/v1/chat"):
        path = path[: -len("/chat")] + "/models"
    elif path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")] + "/models"
    elif path.endswith("/completions"):
        path = path[: -len("/completions")] + "/models"
    elif path.endswith("/chat"):
        path = path[: -len("/chat")] + "/models"
    elif not path.endswith("/models"):
        path = path + "/models"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def preflight_api(api_url: str, timeout: int) -> None:
    models_url = build_models_url(api_url)
    request = urllib.request.Request(models_url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(5, min(timeout, 15))) as response:
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}")
    except Exception as exc:
        raise RuntimeError(f"LM Studio API unavailable at {models_url}: {exc}") from exc


def call_lmstudio(
    image_bytes: bytes,
    mime: str,
    prompt: str,
    api_url: str,
    model: str,
    timeout: int,
    max_tokens: int,
    temperature: float,
    retries: int,
    retry_delay: float,
) -> tuple[str, int | None]:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    body = {
        "model": model,
        "input": [
            {"type": "text", "content": prompt},
            {"type": "image", "data_url": f"data:{mime};base64,{b64}"},
        ],
        "max_output_tokens": max_tokens,
        "temperature": temperature,
        "reasoning": "off",
        "stream": False,
        "store": False,
    }
    payload: dict = {}
    last_error: Exception | None = None
    attempts = max(1, retries)
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            api_url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            last_error = None
            break
        except urllib.error.HTTPError as exc:
            err = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {exc.code}: {err[:500]}")
            if exc.code >= 500 and attempt < attempts:
                time.sleep(retry_delay * attempt)
                continue
            break
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(retry_delay * attempt)
    if last_error is not None:
        raise RuntimeError(f"request failed after {attempts} attempts: {last_error}") from last_error
    messages = [item for item in payload.get("output", []) if item.get("type") == "message"]
    content = "\n".join(str(item.get("content", "")) for item in messages).strip()
    stats = payload.get("stats") or {}
    token_values = [stats.get("input_tokens"), stats.get("total_output_tokens")]
    tokens = sum(value for value in token_values if isinstance(value, int))
    return content, tokens if any(isinstance(value, int) for value in token_values) else None


def extract_json_object(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    candidates = [cleaned]
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def check_consistency(category: str, dims: dict[str, str]) -> str | None:
    """Return a human-readable contradiction description, or None if consistent."""
    nsfw = dims.get("nsfw")
    medium = dims.get("medium")
    if nsfw == "yes" and category not in NSFW_CATEGORIES:
        return f"nsfw=yes but main={category}"
    if nsfw == "no" and category in NSFW_CATEGORIES:
        return f"nsfw=no but main={category}"
    # 2D categories allow medium=photo (anime figure/merchandise photos).
    if medium == "drawn" and category in PHOTO_ONLY_CATEGORIES:
        return f"medium=drawn but main={category}"
    return None


def resolve_nsfw_remap(category: str | None, dims: dict[str, str]) -> tuple[str | None, bool]:
    """NSFW-first ruling: nsfw=yes on a non-NSFW main is remapped to the
    matching NSFW category (drawn -> 二次元_NSFW, photo -> 三次元_NSFW).

    Returns (possibly remapped category, whether a remap happened).
    """
    if category and dims.get("nsfw") == "yes" and category not in NSFW_CATEGORIES:
        target = "三次元_NSFW" if dims.get("medium") == "photo" else "二次元_NSFW"
        return target, True
    return category, False


ALIASES: dict[str, list[str]] = {
    "二次元_NSFW": ["hentai", "galgame", "eroge", "visual novel", "adult game", "explicit anime", "nsfw anime", "nudity", "sexual", "lewd", "gore", "bloody", "mutilation", "body horror", "grotesque"],
    "三次元_NSFW": ["nude", "nudity", "explicit photo", "sexual", "erotic photo", "porn", "real nsfw", "gore", "blood", "bloody", "severe injury", "body horror"],
    "隐私文档": ["id card", "passport", "bank card", "driver license", "medical record", "contract", "identity document", "personal document"],
    "游戏截图": ["gameplay", "hud", "crosshair", "health bar", "minimap", "skill bar", "inventory", "fps", "tps", "rts", "moba", "vrchat", "facecam", "discord", "voice overlay", "login screen", "game menu"],
    "二次元_梗图": ["anime meme", "meme", "reaction", "sticker", "caption", "speech bubble", "watermark", "repost", "forward"],
    "二次元_插画": ["anime", "manga", "illustration", "fan art", "cg artwork", "anime art", "digital painting", "2d", "drawing", "figure photo", "merchandise"],
    "影视综艺截图": ["subtitle", "subtitles", "tv show", "movie screenshot", "variety show", "broadcast", "episode", "danmaku"],
    "资讯文档截图": ["news", "webpage", "chat log", "document", "announcement", "software ui", "spreadsheet", "receipt", "invoice", "scan", "table", "form", "qr code", "payment code", "share card", "map", "blueprint", "medical imaging"],
    "军事装备": ["military", "weapon", "gun", "firearm", "aircraft", "tank", "warship", "missile", "soldier equipment"],
    "动物萌宠": ["animal", "pet", "wildlife", "cat", "dog", "bird"],
    "绘画艺术": ["western comic", "comic book", "manhwa", "oil painting", "watercolor", "sketch", "pixel art", "ukiyo-e", "calligraphy", "fine art"],
    "CG渲染": ["3d render", "architectural visualization", "product render", "pixar", "cgi"],
    "三次元_SFW": ["portrait", "person", "people", "cosplay", "landscape", "street", "event", "travel", "concert", "photograph", "live action"],
    "生活记录": ["food", "product photo", "object", "appliance", "package", "everyday life", "daily record", "room", "desk", "shop display"],
    "无主体": ["abstract", "gradient", "pattern", "wallpaper", "blank", "texture", "no subject", "corrupted"],
}


def parse_category_fallback(text: str, categories: dict[str, str]) -> str | None:
    """Legacy v4-style parser used only when JSON parsing fails."""
    cleaned = text.strip().strip("`'\".,;:，。；：、 \n\r\t")
    for category in categories:
        if cleaned == category:
            return category
    for category in categories:
        if category in cleaned:
            return category
    lowered = cleaned.lower()
    for category in categories:
        for alias in ALIASES.get(category, []):
            if re.search(rf"\b{re.escape(alias)}\b", lowered):
                return category
    return None


def parse_classification(text: str, categories: dict[str, str]) -> tuple[str, str | None, dict[str, str], str | None]:
    """Return (status, category, dims, contradiction) for ONE model answer."""
    obj = extract_json_object(text)
    if obj is not None:
        main = str(obj.get("main", "")).strip().strip("`'\" \n\r\t")
        category = None
        if main in categories:
            category = main
        else:
            for name in categories:
                if name in main:
                    category = name
                    break
        dims: dict[str, str] = {}
        dims_valid = True
        for key in DIMENSION_KEYS:
            value = str(obj.get(key, "")).strip().lower()
            if key == "medium":
                if value in ("drawn", "photo"):
                    dims[key] = value
                else:
                    dims[key] = ""
                    dims_valid = False
            else:
                if value in ("yes", "no"):
                    dims[key] = value
                else:
                    dims[key] = ""
                    dims_valid = False
        if category and dims_valid:
            category, remapped = resolve_nsfw_remap(category, dims)
            contradiction = check_consistency(category, dims)
            if contradiction:
                return "review", category, dims, contradiction
            if remapped:
                return "ok", category, dims, "remapped to NSFW (nsfw=yes priority)"
            return "ok", category, dims, None
        if category:
            return "review", category, dims, "invalid or missing dimension values"
        return "parse_error", None, {}, f"unknown main in JSON: {main[:80]}"

    fallback = parse_category_fallback(text, categories)
    if fallback:
        return "review", fallback, {}, "response was not valid JSON"
    return "parse_error", None, {}, f"Could not parse response: {text[:120]}"


def combine_payload_results(
    parsed_list: list[tuple[str, str | None, dict[str, str], str | None]],
    failed_label: str = "long-image half",
) -> tuple[str, str | None, dict[str, str], str | None]:
    """Combine per-payload results (long image halves) into a final answer.

    Rule: any text-dominated half makes the whole 资讯文档截图; otherwise the
    first valid half decides the main category. Dimensions are OR-merged.
    """
    valid = [p for p in parsed_list if p[0] in ("ok", "review") and p[1]]
    failed = [p for p in parsed_list if p not in valid]
    if not valid:
        note = parsed_list[-1][3] if parsed_list else "no payloads produced"
        return "parse_error", None, {}, str(note)
    mains = [p[1] for p in valid]
    distinct_mains = list(dict.fromkeys(str(main) for main in mains))
    disagreement_note = None
    nsfw_mains = {main for main in distinct_mains if main in NSFW_CATEGORIES}
    if len(distinct_mains) > 1 and (not nsfw_mains or len(nsfw_mains) > 1):
        disagreement_note = f"{failed_label} category disagreement: {' vs '.join(distinct_mains)}"
    nsfw_valid = [p for p in valid if p[1] in NSFW_CATEGORIES]
    if nsfw_valid:
        # NSFW-first ruling: any NSFW half decides the main category.
        main_result = nsfw_valid[0]
    elif TEXT_DOMINANT_CATEGORY in mains:
        main_result = next(p for p in valid if p[1] == TEXT_DOMINANT_CATEGORY)
    else:
        main_result = valid[0]
    dims: dict[str, str] = {}
    for key in DIMENSION_KEYS:
        if key == "medium":
            values = [p[2].get("medium") for p in valid]
            dims[key] = "drawn" if "drawn" in values else ("photo" if "photo" in values else "")
        else:
            values = [p[2].get(key) for p in valid]
            if "yes" in values:
                dims[key] = "yes"
            elif values and all(v == "no" for v in values):
                dims[key] = "no"
            else:
                dims[key] = ""
    contradiction = next((p[3] for p in valid if p[3]), None)
    failure_note = None
    if len(parsed_list) > 1 and failed:
        details = "; ".join(str(p[3] or p[0]) for p in failed)
        failure_note = f"{failed_label} failed: {details}"
    merged_main, remapped = resolve_nsfw_remap(str(main_result[1]), dims)
    merged_check = check_consistency(merged_main, dims)
    review_notes = [merged_check, disagreement_note, failure_note]
    review_notes.extend(p[3] for p in valid if p[0] == "review" and p[3])
    if merged_check or disagreement_note or failure_note:
        return "review", merged_main, dims, "; ".join(note for note in review_notes if note)
    base_status = "review" if any(p[0] == "review" for p in valid) else "ok"
    status = "ok" if remapped and base_status == "ok" else base_status
    note = "remapped to NSFW (nsfw=yes priority)" if remapped else contradiction
    if base_status == "review":
        note = "; ".join(p[3] for p in valid if p[0] == "review" and p[3]) or contradiction
    return status, merged_main, dims, note


def classify_item(
    item: MediaItem,
    prompt: str,
    categories: dict[str, str],
    api_url: str,
    model: str,
    timeout: int,
    max_tokens: int,
    max_dim: int,
    no_resize: bool,
    video_seek: float,
    temperature: float,
    retries: int,
    retry_delay: float,
    video_frames: int = 1,
) -> ClassificationResult:
    started = time.perf_counter()
    try:
        payloads = build_payloads(item, max_dim, no_resize, video_seek, video_frames)
        parsed_list: list[tuple[str, str | None, dict[str, str], str | None]] = []
        raws: list[str] = []
        tokens_total = 0
        tokens_seen = False
        for image_bytes, mime in payloads:
            raw, tokens = call_lmstudio(
                image_bytes=image_bytes,
                mime=mime,
                prompt=prompt,
                api_url=api_url,
                model=model,
                timeout=timeout,
                max_tokens=max_tokens,
                temperature=temperature,
                retries=retries,
                retry_delay=retry_delay,
            )
            raws.append(raw)
            if isinstance(tokens, int):
                tokens_total += tokens
                tokens_seen = True
            parsed_list.append(parse_classification(raw, categories))
        failed_label = "video frame" if item.kind == "video" else "long-image half"
        status, category, dims, contradiction = combine_payload_results(parsed_list, failed_label)
        if status == "parse_error":
            return ClassificationResult(
                item=item,
                category=None,
                dims={},
                raw=" || ".join(raws),
                tokens=tokens_total if tokens_seen else None,
                status="parse_error",
                error=contradiction,
                elapsed=time.perf_counter() - started,
            )
        return ClassificationResult(
            item=item,
            category=category,
            dims=dims,
            raw=" || ".join(raws),
            tokens=tokens_total if tokens_seen else None,
            status=status,
            contradiction=contradiction,
            elapsed=time.perf_counter() - started,
        )
    except Exception as exc:
        return ClassificationResult(
            item=item,
            category=None,
            dims={},
            raw="",
            tokens=None,
            status="error",
            error=str(exc),
            elapsed=time.perf_counter() - started,
        )


def sanitize_category(category: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", category).strip() or "未分类"


def safe_destination(root: Path, category: str, source: Path, preserve_tree: bool, input_root: Path) -> Path:
    category_dir = root / sanitize_category(category)
    if preserve_tree:
        try:
            rel = source.relative_to(input_root)
        except ValueError:
            rel = Path(source.name)
        dest = category_dir / rel
    else:
        dest = category_dir / source.name
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    for i in range(1, 10_000):
        candidate = dest.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"too many duplicate destination names for {dest}")


def is_category_output_dir(name: str, categories: dict[str, str], review_dir: str) -> bool:
    if name == review_dir:
        return True
    for category in categories:
        base = sanitize_category(category)
        if name == base or name.startswith(f"{base}（") or name.startswith(f"{base}("):
            return True
    return False


def destination_category(result: ClassificationResult, review_dir: str) -> str | None:
    if not result.category:
        return None
    if result.status == "ok":
        return result.category
    if result.status == "review":
        return review_dir
    return None


def apply_file_action(
    result: ClassificationResult,
    input_root: Path,
    output_root: Path,
    action: str,
    preserve_tree: bool,
    review_dir: str,
) -> Path | None:
    if result.status not in ("ok", "review") or not result.category:
        return None
    target = destination_category(result, review_dir)
    if not target:
        return None
    dest = safe_destination(output_root, target, result.item.path, preserve_tree, input_root)
    if action == "dry-run":
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if action == "copy":
        shutil.copy2(result.item.path, dest)
    elif action == "move":
        shutil.move(str(result.item.path), str(dest))
    else:
        raise ValueError(f"unknown action: {action}")
    return dest


def make_report_timestamp() -> str:
    """Return a filesystem-safe timestamp for the report created by this run."""
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def run_lms(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["lms", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def ensure_lms_server(api_url: str) -> None:
    if api_url != DEFAULT_API_URL or shutil.which("lms") is None:
        return
    status = run_lms(["server", "status"])
    if status.returncode == 0 and "running" in status.stdout.lower():
        return
    result = run_lms(["server", "start", "--port", "1234", "--bind", "127.0.0.1"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "lms server start failed")


def maybe_load_lms_model(model: str, model_key: str | None, context_length: int | None, api_url: str) -> None:
    if context_length is None:
        return
    if api_url != DEFAULT_API_URL:
        return
    if shutil.which("lms") is None:
        eprint("[warn] lms CLI not found; skipping automatic context load")
        return
    key = model_key or (DEFAULT_LMS_MODEL_KEY if model == DEFAULT_MODEL else model)
    loaded = run_lms(["ps", "--json"])
    if loaded.returncode == 0 and model in loaded.stdout:
        eprint(f"[lms] {model} already loaded")
        return
    eprint(f"[lms] loading {key} as {model} with context {context_length}")
    result = run_lms([
        "load", key,
        "--context-length", str(context_length),
        "--parallel", "1",
        "--gpu", "max",
        "--identifier", model,
        "--yes",
    ])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "lms load failed")


def make_cache_profile(args: argparse.Namespace, prompt: str) -> str:
    config = {
        "version": 8,
        "profile": args.profile,
        "prompt": prompt,
        "api_url": args.api_url,
        "model": args.model,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "max_dim": args.max_dim,
        "no_resize": args.no_resize,
        "video_frames": args.video_frames,
        "video_seek": args.video_seek if args.video_frames <= 1 else None,
        "long_image_ratio": LONG_IMAGE_RATIO,
    }
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"v8:{args.profile}:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def export_report(results: list[ClassificationResult], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("path\tkind\tstatus\tcategory\tnsfw\tmedium\tanimal\tpeople\ttext\tcontradiction\ttokens\telapsed_seconds\terror\traw_response\n")
        for r in results:
            dims = r.dims
            values = [
                str(r.item.path),
                r.item.kind,
                r.status,
                r.category or "",
                dims.get("nsfw", ""),
                dims.get("medium", ""),
                dims.get("animal", ""),
                dims.get("people", ""),
                dims.get("text", ""),
                r.contradiction or "",
                "" if r.tokens is None else str(r.tokens),
                f"{r.elapsed:.3f}",
                r.error or "",
                r.raw.replace("\t", " ").replace("\n", " "),
            ]
            fh.write("\t".join(v.replace("\r", " ") for v in values) + "\n")


def private_item_label(index: int, total: int) -> str:
    return f"item {index}/{total}"


def worker_log_consumer(log_q: queue.Queue[str], stop: threading.Event) -> None:
    while not stop.is_set() or not log_q.empty():
        try:
            print(log_q.get(timeout=0.1), flush=True)
        except queue.Empty:
            pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local LM Studio native vision classifier v8: 15 categories + orthogonal dimensions."
    )
    parser.add_argument("--input", "-i", required=True, help="Directory containing media files.")
    parser.add_argument("--output", "-o", help="Destination root. Defaults to the input directory.")
    parser.add_argument("--profile", default="15", choices=sorted(PROFILES), help="Category profile. Default: 15.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="LM Studio native /api/v1/chat endpoint.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="LM Studio model id used in the request body.")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent requests. Increase carefully for local GPU limits.")
    parser.add_argument("--recursive", action="store_true", help="Scan subdirectories too.")
    parser.add_argument("--include-videos", action="store_true", help="Classify videos using sampled frame(s).")
    parser.add_argument("--video-seek", type=float, default=3.0, help="Seconds into each video for the representative frame.")
    parser.add_argument("--video-frames", type=int, default=1, help="Video frames to classify and combine; 1 keeps the fast single-frame mode.")
    parser.add_argument("--action", choices=["move", "copy", "dry-run"], default="move", help="What to do after classification.")
    parser.add_argument("--preserve-tree", action="store_true", help="Preserve relative paths under category folders.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of pending files to process.")
    parser.add_argument("--max-dim", type=int, default=DEFAULT_MAX_DIM, help="Max image side sent to the model.")
    parser.add_argument("--no-resize", action="store_true", help="Send original image bytes instead of ffmpeg-resized JPEG.")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="Max completion tokens.")
    parser.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature.")
    parser.add_argument("--timeout", type=int, default=180, help="HTTP request timeout seconds.")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Retry count for transient LM Studio request failures.")
    parser.add_argument("--retry-delay", type=float, default=DEFAULT_RETRY_DELAY, help="Base seconds between LM Studio retries.")
    parser.add_argument("--review-dir", default=DEFAULT_REVIEW_DIR, help="Folder for contradictory or low-quality answers. Default: 未分类.")
    parser.add_argument("--logs-dir", help="Directory for the state DB and TSV reports. Defaults to <output>/_logs.")
    parser.add_argument("--db", help="SQLite progress database. Defaults to <logs-dir>/.classification_state_v8.sqlite3.")
    parser.add_argument("--report", help="TSV report path. Defaults to <logs-dir>/classification_report_v8_<YYYYMMDD_HHMMSS>.tsv.")
    parser.add_argument("--force", action="store_true", help="Ignore cached successful results and reclassify.")
    parser.add_argument("--lms-context", type=int, default=8192, help="Use lms CLI to load model with this context length before running.")
    parser.add_argument("--lms-model-key", help="Model key passed to lms load; defaults to the selected model.")
    parser.add_argument("--private-logs", action="store_true", help="Do not print media file names in console logs.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    args = parse_args(argv or sys.argv[1:])

    input_root = Path(args.input).expanduser().resolve()
    if not input_root.is_dir():
        eprint(f"[err] input directory does not exist: {input_root}")
        return 2
    output_root = Path(args.output).expanduser().resolve() if args.output else input_root
    output_root.mkdir(parents=True, exist_ok=True)

    log_dir = Path(args.logs_dir).expanduser().resolve() if args.logs_dir else output_root / "_logs"
    db_path = Path(args.db).expanduser().resolve() if args.db else log_dir / ".classification_state_v8.sqlite3"
    report_path = Path(args.report).expanduser().resolve() if args.report else log_dir / f"classification_report_v8_{make_report_timestamp()}.tsv"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    categories = PROFILES[args.profile]
    prompt = build_prompt(categories)
    cache_profile = make_cache_profile(args, prompt)

    try:
        ensure_lms_server(args.api_url)
        maybe_load_lms_model(args.model, args.lms_model_key, args.lms_context, args.api_url)
    except Exception as exc:
        eprint(f"[err] {exc}")
        return 2
    try:
        preflight_api(args.api_url, args.timeout)
    except Exception as exc:
        eprint(f"[err] {exc}")
        return 2

    if output_root != input_root:
        excludes = {output_root}
    else:
        excludes = set()
        for child in output_root.iterdir():
            if child.is_dir() and is_category_output_dir(child.name, categories, args.review_dir):
                excludes.add(child)
    excludes.add(output_root / "_codex_video_frames")
    items = scan_media(input_root, args.recursive, args.include_videos, excludes)
    store = ProgressStore(db_path)
    results: list[ClassificationResult] = []

    try:
        pending: list[MediaItem] = []
        for item in items:
            if args.force:
                pending.append(item)
                continue
            cached = store.get_done(item, cache_profile, args.model)
            if cached:
                cached_result = ClassificationResult(
                    item=item,
                    category=cached["category"],
                    dims=cached["dims"],
                    raw="[cached]",
                    tokens=None,
                    status=cached["status"],
                    contradiction=cached["contradiction"],
                    elapsed=0.0,
                )
                if args.action != "dry-run":
                    apply_file_action(cached_result, input_root, output_root, args.action, args.preserve_tree, args.review_dir)
                results.append(cached_result)
            else:
                pending.append(item)

        if args.limit > 0:
            pending = pending[: args.limit]

        print("=" * 72)
        print("LM Studio media classifier v8 (15 categories + dimensions)")
        print(f"input:        {input_root}")
        print(f"output:       {output_root}")
        print(f"profile:      {args.profile} ({len(categories)} categories)")
        print(f"model:        {args.model}")
        print(f"api:          {args.api_url}")
        print(f"workers:      {args.workers}")
        print(f"action:       {args.action}")
        print(f"review dir:   {args.review_dir}")
        print(f"scanned:      {len(items)}")
        print(f"pending:      {len(pending)}")
        print(f"cached done:  {len(results)}")
        print("=" * 72)

        if not pending:
            export_report(results, report_path)
            print(f"[done] nothing pending. report: {report_path}")
            return 0

        log_q: queue.Queue[str] = queue.Queue()
        stop_log = threading.Event()
        log_thread = threading.Thread(target=worker_log_consumer, args=(log_q, stop_log), daemon=True)
        log_thread.start()

        ok = reviews = errors = 0
        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            future_map = {
                executor.submit(
                    classify_item,
                    item,
                    prompt,
                    categories,
                    args.api_url,
                    args.model,
                    args.timeout,
                    args.max_tokens,
                    args.max_dim,
                    args.no_resize,
                    args.video_seek,
                    args.temperature,
                    args.retries,
                    args.retry_delay,
                    args.video_frames,
                ): item
                for item in pending
            }
            total = len(future_map)
            for index, future in enumerate(concurrent.futures.as_completed(future_map), 1):
                result = future.result()
                store.save(result, cache_profile, args.model)
                dest = None
                if result.status in ("ok", "review"):
                    if result.status == "ok":
                        ok += 1
                    else:
                        reviews += 1
                    dest = apply_file_action(result, input_root, output_root, args.action, args.preserve_tree, args.review_dir)
                else:
                    errors += 1
                results.append(result)
                speed = index / max(time.perf_counter() - started, 0.001)
                if result.status in ("ok", "review"):
                    token_text = "" if result.tokens is None else f", {result.tokens} tokens"
                    dest_text = "" if dest is None else f" -> {dest.parent.name}/{dest.name}"
                    item_label = private_item_label(index, total) if args.private_logs else result.item.path.name
                    if args.private_logs:
                        dest_text = "" if dest is None else f" -> {dest.parent.name}/<redacted>"
                    dims_text = "/".join(result.dims.get(k, "?") for k in DIMENSION_KEYS)
                    review_note = "" if result.status == "ok" else f" [REVIEW: {result.contradiction}]"
                    log_q.put(
                        f"[{index}/{total}] {result.status} {result.category} ({dims_text}): {item_label}"
                        f" ({result.elapsed:.1f}s{token_text}, {speed:.2f}/s){dest_text}{review_note}"
                    )
                else:
                    item_label = private_item_label(index, total) if args.private_logs else result.item.path.name
                    log_q.put(
                        f"[{index}/{total}] {result.status}: {item_label}"
                        f" ({result.elapsed:.1f}s) {result.error}"
                    )

        stop_log.set()
        log_thread.join(timeout=2)
        export_report(results, report_path)

        print("=" * 72)
        print(f"ok:       {ok}")
        print(f"review:   {reviews} -> {args.review_dir}/")
        print(f"errors:   {errors}")
        print(f"report:   {report_path}")
        print(f"state db: {db_path}")
        return 0 if errors == 0 else 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
