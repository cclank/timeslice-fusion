#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "openai>=1.0.0",
#     "dashscope>=1.25.8",
#     "requests>=2.31.0",
#     "pillow>=10.0.0",
#     "numpy>=1.24.0",
# ]
# ///

"""
TimeSlice Fusion — 时空融影 v3

将 360° 全景风景视频 + 自拍照融合为"你在风景中"的电影级短视频。

v3 双引擎:
  i2v (默认) — 真实帧合成 + I2V 动画化，风景100%还原
  r2v (备选) — R2V 参考图生视频，AI 重绘全部内容

Pipeline (i2v):
  0. preprocess_selfie   — 裁剪头肩构图
  0b. remove_background  — macOS Vision 人像抠图
  1. extract_360_frames  — ffmpeg v360 提取多角度候选帧
  2. select_best_frame   — qwen-vl 两轮选帧
  3. analyze_scene       — qwen-vl 深度场景分析
  4. analyze_person      — qwen-vl 人物特征提取
  5. build_i2v_prompt    — 运动提示词 (仅描述动作，不描述场景)
  5b. composite_person   — 人物合成到真实帧 (自然融合/艺术拼贴)
  6. generate_i2v_video  — I2V 动画化 (合成图为首帧，只加运动)
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path

# Fix encoding
import locale
try:
    locale.setlocale(locale.LC_ALL, "C.UTF-8")
except locale.Error:
    pass
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# Swift background removal script path
REMOVE_BG_SCRIPT = str(Path(__file__).parent / "remove_bg.swift")

# All output types
ALL_OUTPUTS = ["video", "gif", "vertical", "cover", "captioned"]

# 6 directions for 360° frame extraction (yaw angles)
DIRECTIONS = [
    ("front",      0),
    ("right",     90),
    ("back",     180),
    ("left",     270),
    ("up_right",  45),
    ("up_left",  315),
]

# ── Negative prompt (shared base for all styles) ──
NEGATIVE_PROMPT_BASE = (
    "deformed face, warped features, face morphing, identity drift, "
    "asymmetrical pupils, lopsided eyes, extra teeth, melting, expression drift, "
    "extra fingers, deformed hands, bad anatomy, duplicate limbs, distorted wrists, extra limbs, "
    "soft focus, motion smear, ghosting, out of focus, "
    "low quality, blurry, watermark, text, "
    "plastic skin, waxy skin, over-smoothed skin"
)

# ── Identity preservation suffix (appended to every prompt) ──
IDENTITY_SUFFIX = (
    "consistent identity, same person as reference, "
    "photorealistic, natural skin texture, detailed face"
)

# ── Style presets ──
STYLE_PRESETS = {
    "cinematic": {
        "camera":   "slow dolly forward, shallow depth of field",
        "lighting": "natural cinematic lighting with soft shadows",
        "action":   "standing still, gazing into the distance",
        "negative": "cartoon, anime",
    },
    "dreamy": {
        "camera":   "gentle floating drift, soft focus edges, ethereal slow motion",
        "lighting": "diffused golden light, soft bokeh particles",
        "action":   "standing with eyes half-closed, serene expression",
        "negative": "harsh light, sharp edges, dark shadows",
    },
    "epic": {
        "camera":   "dramatic low-angle wide shot, sweeping crane upward",
        "lighting": "dramatic chiaroscuro, god rays breaking through clouds",
        "action":   "standing at the edge, arms slightly open, facing the vastness",
        "negative": "indoor, small space, flat lighting",
    },
    "warm": {
        "camera":   "medium shot, natural handheld feel, intimate framing",
        "lighting": "warm golden hour backlight, soft rim light on hair",
        "action":   "smiling gently, relaxed posture, turning toward camera",
        "negative": "cold tones, dark mood, harsh contrast",
    },
    "noir": {
        "camera":   "high contrast, dramatic Dutch angle, film noir shadows",
        "lighting": "harsh single-source light, deep shadows",
        "action":   "leaning slightly, half-face in shadow, contemplative gaze",
        "negative": "bright colors, cheerful, sunny",
    },
    "vintage": {
        "camera":   "slight vignette, warm film grain, soft muted tones",
        "lighting": "warm tungsten light, slightly overexposed highlights",
        "action":   "looking down gently, relaxed natural posture",
        "negative": "modern, sharp digital, neon, cold blue tones",
    },
    "anime": {
        "camera":   "wide establishing shot, vivid cel shading, anime style",
        "lighting": "bright anime sky, dramatic light rays, sparkle effects",
        "action":   "standing with wind in hair, looking toward horizon",
        "negative": "photorealistic, dark, gritty, muted colors",
    },
}

# ── Scene → simple action mapping (low-motion to reduce distortion) ──
SCENE_ACTION_MAP = {
    "mountain":   "standing on a ridge, gazing at distant peaks",
    "lake":       "standing at the water's edge, looking at the reflection",
    "beach":      "standing at the shoreline, facing the ocean",
    "ocean":      "standing on a cliff overlooking the ocean",
    "forest":     "standing on a forest path, soft light on face",
    "desert":     "standing on a sand dune, looking at the horizon",
    "city":       "standing in the city street, confident posture",
    "field":      "standing in a wildflower meadow, gentle breeze",
    "snow":       "standing in fresh snow, peaceful silence",
    "sunset":     "standing against the sunset sky, golden light",
    "waterfall":  "standing near the waterfall, mist around",
    "temple":     "standing in ancient corridors, dappled light",
    "garden":     "standing in the garden, flowers around",
    "bridge":     "standing on a bridge, looking out at the view",
    "river":      "standing by the riverbank, calm water nearby",
}

# Lighting adaptation: (time_of_day, direction_hint) → lighting desc
LIGHTING_ADAPTATION = {
    ("golden_hour", "front"):   "warm amber backlight creating a glowing rim",
    ("golden_hour", "back"):    "rich golden light illuminating face warmly",
    ("golden_hour", "side"):    "long side-shadows with golden fill light",
    ("blue_hour",   None):      "cool blue twilight with warm accent lights",
    ("midday",      None):      "bright sun with vivid colors",
    ("overcast",    None):      "soft diffused lighting, gentle tones",
    ("night",       None):      "moonlight and ambient glow, quiet atmosphere",
    ("sunrise",     None):      "first light on the horizon, pastel sky",
}

# ── I2V motion presets (only describe motion, NOT scene content) ──
I2V_MOTION_PRESETS = {
    "cinematic": {
        "motion": "Smooth horizontal camera orbit 180 degrees around the person, light breeze through hair, cinematic tracking shot",
        "negative": "static camera, head rotation, fast motion, jerky movement, morphing, distortion, scene change",
    },
    "dreamy": {
        "motion": "Slow dreamy horizontal camera rotation around the person, soft hair movement in wind, ethereal orbit",
        "negative": "static camera, sharp movement, sudden change, distortion, morphing face",
    },
    "epic": {
        "motion": "Dramatic slow horizontal camera sweep 180 degrees around the person, wind in hair and clothes, epic orbital tracking",
        "negative": "static camera, boring, fast motion, morphing, distortion",
    },
    "warm": {
        "motion": "Gentle horizontal camera orbit around the person, warm light shifting across face, natural tracking movement",
        "negative": "static camera, cold tones, fast motion, morphing, distortion",
    },
    "noir": {
        "motion": "Slow dramatic horizontal camera rotation around the person, shadows shifting across face, noir orbit",
        "negative": "static camera, bright colors, fast motion, morphing, distortion",
    },
    "vintage": {
        "motion": "Nostalgic slow horizontal camera pan orbiting around the person, light wind in hair, vintage tracking",
        "negative": "static camera, modern effects, fast motion, morphing, distortion",
    },
    "anime": {
        "motion": "Dynamic horizontal camera sweep around the person, hair flowing in wind, sparkle effects drift",
        "negative": "static camera, photorealistic, fast motion, morphing",
    },
}

# ── Light adaptation for person compositing ──
COMPOSITE_LIGHT_ADAPT = {
    "golden_hour": {"brightness": 1.08, "contrast": 0.95, "warmth": 10},
    "sunset":      {"brightness": 1.05, "contrast": 0.92, "warmth": 15},
    "sunrise":     {"brightness": 1.05, "contrast": 0.95, "warmth": 8},
    "overcast":    {"brightness": 0.95, "contrast": 0.98, "warmth": 0},
    "blue_hour":   {"brightness": 0.88, "contrast": 1.02, "warmth": -5},
    "night":       {"brightness": 0.80, "contrast": 1.05, "warmth": -5},
    "midday":      {"brightness": 1.00, "contrast": 1.00, "warmth": 0},
}


# ──────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────

@dataclass
class FrameInfo:
    path: str
    timestamp: float
    direction: str
    yaw: int

@dataclass
class SceneAnalysis:
    scene_type: str = ""
    time_of_day: str = ""
    weather: str = ""
    dominant_colors: list[str] = field(default_factory=list)
    key_elements: list[str] = field(default_factory=list)
    mood: str = ""
    depth_layers: list[str] = field(default_factory=list)
    suggested_person_position: str = ""
    cinematic_description: str = ""

@dataclass
class PersonAnalysis:
    appearance: str = ""
    clothing: str = ""
    hair: str = ""
    expression: str = ""
    style_vibe: str = ""

@dataclass
class Config:
    video: str = ""
    selfie: str = ""
    output: str = "./timeslice_output.mp4"
    style: str = "cinematic"
    model: str = ""
    vl_model: str = "qwen-vl-max"
    duration: int = 5
    size: str = "1280*720"
    top_n: int = 6
    api_key: str = ""
    bailian_dir: str = ""
    work_dir: str = ""
    # v2: multi-output
    outputs: list[str] = field(default_factory=lambda: ["video"])
    caption: str = ""
    gif_fps: int = 12
    gif_width: int = 480
    font: str = ""
    # v3: engine + composite
    engine: str = "i2v"
    composite_style: str = "natural"
    # v3.1: multi-shot
    shots: int = 1
    transition: str = "crossfade"
    transition_duration: float = 0.5

    def __post_init__(self):
        if not self.model:
            self.model = "wan2.6-i2v" if self.engine == "i2v" else "wan2.6-r2v"


# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

def log(msg: str):
    print(f"[TimeSlice] {msg}", file=sys.stderr, flush=True)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def get_api_key(provided: str | None) -> str:
    key = provided or os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        cfg = Path.home() / ".config" / "bailian-multimodal" / "api_key.txt"
        if cfg.exists():
            key = cfg.read_text().strip()
    if not key:
        log("ERROR: DASHSCOPE_API_KEY not found.")
        sys.exit(1)
    return key


def find_bailian_dir(provided: str | None) -> str:
    """Find bailian-multimodal-skills directory."""
    if provided and os.path.isdir(provided):
        return provided
    candidates = [
        Path.home() / "code" / "openclaw_provider_plugins" / "bailian-multimodal-skills",
        Path.home() / ".qoderwork" / "skills" / "bailian-multimodal-skills",
        Path.home() / ".agents" / "skills" / "bailian-multimodal-skills",
    ]
    for c in candidates:
        if (c / "scripts" / "run_multimodal.py").exists():
            return str(c)
    log("ERROR: bailian-multimodal-skills not found. Use --bailian-dir.")
    sys.exit(1)


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except Exception:
        log("WARNING: Cannot detect video duration, defaulting to 10s")
        return 10.0


def get_video_dimensions(video_path: str) -> tuple[int, int]:
    """Get video width x height via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0:s=x", video_path],
            capture_output=True, text=True, timeout=30,
        )
        w, h = r.stdout.strip().split("x")
        return int(w), int(h)
    except Exception:
        return 0, 0


def is_equirectangular(video_path: str) -> bool:
    """Detect if video is equirectangular (aspect ratio >= 1.8)."""
    w, h = get_video_dimensions(video_path)
    if w == 0 or h == 0:
        return False
    return (w / h) >= 1.8


def image_to_base64_url(path: str) -> str:
    """Convert local image to base64 data URL."""
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    ext = Path(path).suffix.lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")
    return f"data:{mime};base64,{data}"


def call_vl(config: Config, images: list[str], system_prompt: str, user_prompt: str) -> str:
    """Call qwen-vl model with images + text prompt. Returns text response."""
    from openai import OpenAI
    client = OpenAI(api_key=config.api_key, base_url=DASHSCOPE_BASE_URL)

    content: list[dict] = []
    for img in images:
        if img.startswith(("http://", "https://", "data:")):
            content.append({"type": "image_url", "image_url": {"url": img}})
        else:
            content.append({"type": "image_url", "image_url": {"url": image_to_base64_url(img)}})
    content.append({"type": "text", "text": user_prompt})

    resp = client.chat.completions.create(
        model=config.vl_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        temperature=0.7,
        max_tokens=2000,
    )
    return resp.choices[0].message.content or ""


def parse_json_from_response(text: str) -> dict:
    """Extract JSON from VL model response (handles ```json blocks)."""
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def get_output_path(config: Config, suffix: str, ext: str = ".mp4") -> str:
    """Derive output path for a specific output type."""
    base = config.output
    if os.path.isdir(base):
        return os.path.join(base, f"timeslice_{config.style}{suffix}{ext}")
    # Single file mode: insert suffix before extension
    stem, orig_ext = os.path.splitext(base)
    if suffix:
        return f"{stem}{suffix}{ext}"
    return f"{stem}{ext}" if ext != orig_ext else base


def load_cjk_font(size: int = 36, font_path: str = "") -> "ImageFont.FreeTypeFont":
    """Load a CJK-compatible font for text overlay."""
    from PIL import ImageFont

    candidates = [font_path] if font_path else []
    candidates.extend([
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ])
    for fp in candidates:
        if fp and os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    log("  WARNING: No CJK font found, using default font")
    return ImageFont.load_default()


# ──────────────────────────────────────────────
# Step 0: Selfie Preprocessing
# ──────────────────────────────────────────────

def preprocess_selfie(config: Config) -> str:
    """Crop selfie to head-shoulder framing for better R2V quality."""
    log("Step 0: Preprocessing selfie (head-shoulder crop)...")
    from PIL import Image

    img = Image.open(config.selfie)
    w, h = img.size

    # Crop to upper 70% (head-shoulder framing)
    crop_h = int(h * 0.7)
    if crop_h < h:
        img = img.crop((0, 0, w, crop_h))
        log(f"  -> Cropped to head-shoulder: {w}x{crop_h} (from {w}x{h})")

    # Save as JPEG for best R2V compatibility
    out = str(Path(config.work_dir) / "selfie_processed.jpg")
    img.convert("RGB").save(out, "JPEG", quality=95)
    log(f"  -> Saved preprocessed selfie: {out}")
    return out


# ──────────────────────────────────────────────
# Step 0b: Background Removal (I2V only)
# ──────────────────────────────────────────────

def remove_background(selfie_path: str, work_dir: str) -> str:
    """Remove selfie background using macOS Vision framework (Swift)."""
    log("Step 0b: Removing selfie background (macOS Vision)...")

    output_path = str(Path(work_dir) / "selfie_cutout.png")

    if not os.path.exists(REMOVE_BG_SCRIPT):
        log(f"ERROR: remove_bg.swift not found at {REMOVE_BG_SCRIPT}")
        sys.exit(1)

    try:
        result = subprocess.run(
            ["swift", REMOVE_BG_SCRIPT, selfie_path, output_path],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        log("ERROR: Background removal timed out (60s)")
        sys.exit(1)

    if result.returncode != 0:
        log(f"ERROR: Background removal failed: {result.stderr.strip()}")
        sys.exit(1)

    if not os.path.exists(output_path):
        log("ERROR: Background removal produced no output")
        sys.exit(1)

    # Validate: check non-transparent pixel ratio
    from PIL import Image
    import numpy as np
    img = Image.open(output_path).convert("RGBA")
    alpha = img.split()[3]
    alpha_arr = np.array(alpha)
    non_transparent = int(np.sum(alpha_arr > 10))
    total = alpha_arr.size
    ratio = non_transparent / total if total else 0

    if ratio < 0.05:
        log(f"ERROR: No person detected in selfie (only {ratio:.1%} non-transparent)")
        sys.exit(1)

    log(f"  -> Cutout saved: {output_path} ({ratio:.0%} person area)")
    return output_path


# ──────────────────────────────────────────────
# Step 5b: Person Compositing (I2V only)
# ──────────────────────────────────────────────

def _adjust_warmth(img: "Image.Image", warmth: int) -> "Image.Image":
    """Adjust color temperature: positive=warmer(+R-B), negative=cooler."""
    import numpy as np
    arr = np.array(img, dtype=np.int16)
    arr[:, :, 0] = np.clip(arr[:, :, 0] + warmth, 0, 255)
    arr[:, :, 2] = np.clip(arr[:, :, 2] - warmth, 0, 255)
    from PIL import Image
    return Image.fromarray(arr.astype(np.uint8))


def _create_drop_shadow(person: "Image.Image", blur: int = 12, opacity: float = 0.3) -> "Image.Image":
    """Create a soft drop shadow from person alpha channel."""
    from PIL import Image, ImageFilter
    alpha = person.split()[3]
    shadow_alpha = alpha.point(lambda p: int(p * opacity))
    shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(radius=blur))
    shadow_layer = Image.new("RGBA", person.size, (0, 0, 0, 255))
    shadow_layer.putalpha(shadow_alpha)
    return shadow_layer


def _add_border(img: "Image.Image", width: int, color: tuple) -> "Image.Image":
    """Add outline border around non-transparent area."""
    from PIL import Image, ImageFilter
    alpha = img.split()[3]
    dilated = alpha.filter(ImageFilter.MaxFilter(size=width * 2 + 1))

    import numpy as np
    border_mask = np.clip(
        np.array(dilated, dtype=np.int16) - np.array(alpha, dtype=np.int16),
        0, 255
    ).astype(np.uint8)

    border_layer = Image.new("RGBA", img.size, color)
    border_layer.putalpha(Image.fromarray(border_mask))

    result = Image.new("RGBA", img.size, (0, 0, 0, 0))
    result = Image.alpha_composite(result, border_layer)
    result = Image.alpha_composite(result, img)
    return result


def _crop_to_vertical(img: "Image.Image") -> "Image.Image":
    """Center-crop image to 9:16 aspect ratio."""
    w, h = img.size
    target_ratio = 9 / 16
    current_ratio = w / h
    if current_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))


def composite_person(bg_path: str, cutout_path: str, scene: SceneAnalysis,
                     config: Config, shot_index: int = 0) -> str:
    """Composite person cutout onto real scenery frame."""
    log(f"Step 5b: Compositing person onto real frame ({config.composite_style} mode)...")
    from PIL import Image, ImageFilter, ImageEnhance

    bg = Image.open(bg_path).convert("RGB")
    person = Image.open(cutout_path).convert("RGBA")
    bg_w, bg_h = bg.size

    if config.composite_style == "collage":
        result = _composite_collage(bg, person, scene)
    else:
        result = _composite_natural(bg, person, scene)

    out = str(Path(config.work_dir) / f"composite_{config.composite_style}_shot{shot_index:02d}.jpg")
    result.convert("RGB").save(out, "JPEG", quality=95)
    log(f"  -> Composite saved: {out}")
    return out


def _composite_natural(bg: "Image.Image", person: "Image.Image",
                       scene: SceneAnalysis) -> "Image.Image":
    """Natural blend: looks like a real portrait photo taken at the location.

    Strategy: person in foreground (large, close to camera), scenery behind.
    This matches how travel selfies/portraits actually look — NOT a tiny figure
    standing far away in the scene.
    """
    from PIL import Image, ImageFilter, ImageEnhance
    import numpy as np

    bg_w, bg_h = bg.size

    # 1. Crop cutout to tight bounding box (remove excess transparent space)
    alpha_arr = np.array(person.split()[3])
    rows = np.any(alpha_arr > 10, axis=1)
    cols = np.any(alpha_arr > 10, axis=0)
    if rows.any() and cols.any():
        rmin, rmax = np.where(rows)[0][[0, -1]]
        cmin, cmax = np.where(cols)[0][[0, -1]]
        pad = int(max(rmax - rmin, cmax - cmin) * 0.02)
        rmin = max(0, rmin - pad)
        rmax = min(person.size[1] - 1, rmax + pad)
        cmin = max(0, cmin - pad)
        cmax = min(person.size[0] - 1, cmax + pad)
        person = person.crop((cmin, rmin, cmax + 1, rmax + 1))
        log(f"    Tight-cropped cutout: {person.size[0]}x{person.size[1]}")

    p_w, p_h = person.size

    # 2. Scale: person occupies ~60% of frame height (close foreground portrait)
    target_h = int(bg_h * 0.60)
    scale = target_h / p_h
    target_w = int(p_w * scale)

    # Ensure person doesn't overflow frame width
    if target_w > int(bg_w * 0.85):
        target_w = int(bg_w * 0.85)
        scale = target_w / p_w
        target_h = int(p_h * scale)

    person_scaled = person.resize((target_w, target_h), Image.LANCZOS)
    log(f"    Portrait scale: {target_w}x{target_h} in {bg_w}x{bg_h} frame")

    # 3. Position: center horizontally, bottom extends to frame edge
    #    Person's bottom edge at frame bottom — like a real photo crop
    x = (bg_w - target_w) // 2
    y = bg_h - target_h  # bottom-aligned

    # 4. Light adaptation based on scene time_of_day
    tod = scene.time_of_day or "midday"
    adapt = COMPOSITE_LIGHT_ADAPT.get(tod, COMPOSITE_LIGHT_ADAPT["midday"])

    person_rgb = person_scaled.convert("RGB")
    alpha_channel = person_scaled.split()[3]

    if adapt["brightness"] != 1.0:
        person_rgb = ImageEnhance.Brightness(person_rgb).enhance(adapt["brightness"])
    if adapt["contrast"] != 1.0:
        person_rgb = ImageEnhance.Contrast(person_rgb).enhance(adapt["contrast"])
    if adapt["warmth"] != 0:
        person_rgb = _adjust_warmth(person_rgb, adapt["warmth"])

    person_adapted = person_rgb.copy()
    person_adapted.putalpha(alpha_channel)

    # 5. Edge feathering: gentle blur on alpha edges (sides + top only)
    #    NO bottom fade — person naturally extends to frame bottom
    alpha_blurred = alpha_channel.filter(ImageFilter.GaussianBlur(radius=3))
    person_adapted.putalpha(alpha_blurred)

    # 6. Composite (no drop shadow — person is in foreground, not on ground)
    result = bg.copy().convert("RGBA")
    result.paste(person_adapted, (x, y), person_adapted)
    return result.convert("RGB")


def _composite_collage(bg: "Image.Image", person: "Image.Image",
                       scene: SceneAnalysis) -> "Image.Image":
    """Artistic collage: person clearly 'pasted' onto scenery with style."""
    from PIL import Image
    import numpy as np

    bg_w, bg_h = bg.size

    # 1. Crop to tight bounding box
    alpha_arr = np.array(person.split()[3])
    rows = np.any(alpha_arr > 10, axis=1)
    cols = np.any(alpha_arr > 10, axis=0)
    if rows.any() and cols.any():
        rmin, rmax = np.where(rows)[0][[0, -1]]
        cmin, cmax = np.where(cols)[0][[0, -1]]
        pad = int(max(rmax - rmin, cmax - cmin) * 0.02)
        rmin = max(0, rmin - pad)
        rmax = min(person.size[1] - 1, rmax + pad)
        cmin = max(0, cmin - pad)
        cmax = min(person.size[0] - 1, cmax + pad)
        person = person.crop((cmin, rmin, cmax + 1, rmax + 1))

    # 2. Scale to ~35% of frame height
    target_h = int(bg_h * 0.35)
    scale = target_h / person.size[1]
    target_w = int(person.size[0] * scale)
    person_scaled = person.resize((target_w, target_h), Image.LANCZOS)

    # 2. Add border (white for dark scenes, dark for light scenes)
    tod = scene.time_of_day or "midday"
    border_color = (255, 255, 255, 255) if tod in ("night", "blue_hour") else (40, 40, 40, 255)
    person_bordered = _add_border(person_scaled, width=2, color=border_color)

    # 3. Slight rotation for dynamism
    person_rotated = person_bordered.rotate(
        -2.5, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0),
    )

    # 4. Position: center-bottom, slightly offset
    r_w, r_h = person_rotated.size
    x = (bg_w - r_w) // 2 + int(bg_w * 0.02)
    y = int(bg_h * 0.72) - r_h
    y = max(0, min(y, bg_h - r_h))

    # 5. Composite
    result = bg.copy().convert("RGBA")
    result.paste(person_rotated, (x, y), person_rotated)
    return result.convert("RGB")


# ──────────────────────────────────────────────
# Step 5 (I2V): Motion Prompt
# ──────────────────────────────────────────────

def build_i2v_prompt(scene: SceneAnalysis, config: Config) -> tuple[str, str]:
    """Build I2V motion prompt — only describes movement, NOT scene content."""
    log(f"Step 5: Building I2V motion prompt ({config.style} style)...")

    default = {
        "motion": "Person gently turns head, light breeze moves hair, slow camera pan",
        "negative": "fast motion, morphing, distortion, unnatural movement, scene change",
    }
    preset = I2V_MOTION_PRESETS.get(config.style, default)

    motion = preset["motion"]
    negative = preset["negative"]

    # Add water dynamics if scene has water elements
    elements_str = " ".join(scene.key_elements).lower() if scene.key_elements else ""
    if any(w in elements_str for w in ("water", "sea", "ocean", "lake", "river")):
        motion += ", water ripples softly"

    # Add warm light shift for golden hour
    if scene.time_of_day in ("golden_hour", "sunset"):
        motion += ", warm golden light shifts gently"

    # Keep prompt short (I2V works better with concise prompts)
    if len(motion) > 150:
        motion = motion[:150].rsplit(",", 1)[0]

    log(f"  -> Motion prompt ({len(motion)} chars): {motion}")

    # Save for debugging
    prompt_file = Path(config.work_dir) / "i2v_prompt.txt"
    prompt_file.write_text(f"Motion: {motion}\nNegative: {negative}", encoding="utf-8")

    return motion, negative


# ──────────────────────────────────────────────
# Step 6 (I2V): Generate I2V Video
# ──────────────────────────────────────────────

def generate_i2v_video(composite_path: str, motion_prompt: str,
                       negative_prompt: str, config: Config,
                       label: str = "video", resolution: str = "720P") -> str:
    """Generate video using Wan2.6 I2V — composite image as first frame."""
    log(f"  Generating I2V {label}: {config.model}, resolution={resolution}, "
        f"duration={config.duration}s...")

    bailian_dir = find_bailian_dir(config.bailian_dir)
    script = os.path.join(bailian_dir, "scripts", "run_multimodal.py")

    cmd = [
        "uv", "run", "--directory", bailian_dir, "python", script,
        "--mode", "i2v",
        "--model", config.model,
        "--img-url", os.path.abspath(composite_path),
        "--resolution", resolution,
        "--duration", str(config.duration),
        "--output", os.path.abspath(config.output),
        "--no-audio",
        "--no-prompt-extend",
    ]
    if motion_prompt:
        cmd.extend(["--prompt", motion_prompt])
    if negative_prompt:
        cmd.extend(["--negative-prompt", negative_prompt])
    if config.api_key:
        cmd.extend(["--api-key", config.api_key])

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        for line in proc.stderr:
            line = line.strip()
            if line:
                log(f"    [I2V] {line}")
        proc.wait()

        stdout = proc.stdout.read() if proc.stdout else ""
        if stdout.strip():
            for line in stdout.strip().split("\n"):
                if line.startswith("MEDIA:"):
                    log(f"    -> I2V output: {line.replace('MEDIA:', '').strip()}")
                print(line)

        if proc.returncode != 0:
            log(f"ERROR: I2V process exited with code {proc.returncode}")
            sys.exit(1)

    except Exception as e:
        log(f"ERROR: I2V generation failed: {e}")
        sys.exit(1)

    if os.path.exists(config.output) and os.path.getsize(config.output) > 0:
        size_mb = os.path.getsize(config.output) / (1024 * 1024)
        log(f"    -> {label} generated: {config.output} ({size_mb:.1f} MB)")
    else:
        log(f"ERROR: Output not found: {config.output}")
        sys.exit(1)

    return config.output


# ──────────────────────────────────────────────
# Step 1: Frame Extraction
# ──────────────────────────────────────────────

def extract_360_frames(config: Config) -> list[FrameInfo]:
    """Extract candidate frames from 360° video."""
    log("Step 1: Extracting candidate frames from video...")

    work = Path(config.work_dir) / "frames"
    work.mkdir(parents=True, exist_ok=True)

    duration = get_video_duration(config.video)
    equirect = is_equirectangular(config.video)
    time_points = [duration * p for p in (0.25, 0.50, 0.75)]

    frames: list[FrameInfo] = []

    if equirect:
        log(f"  Detected equirectangular video ({duration:.1f}s). Extracting 6 directions x 3 timepoints...")
        for t in time_points:
            for dname, yaw in DIRECTIONS:
                out = str(work / f"{dname}_{t:.1f}s.jpg")
                cmd = [
                    "ffmpeg", "-y", "-ss", str(t), "-i", config.video,
                    "-vf", f"v360=equirect:rectilinear:yaw={yaw}:pitch=0:h_fov=90:w_fov=110",
                    "-frames:v", "1", "-q:v", "2", out,
                ]
                try:
                    subprocess.run(cmd, capture_output=True, timeout=30)
                    if os.path.exists(out) and os.path.getsize(out) > 0:
                        frames.append(FrameInfo(path=out, timestamp=t, direction=dname, yaw=yaw))
                except Exception as e:
                    log(f"  WARNING: Failed to extract {dname}@{t:.1f}s: {e}")
    else:
        log(f"  Normal video ({duration:.1f}s). Extracting frames at multiple timepoints...")
        n_frames = min(18, max(6, int(duration)))
        time_points = [duration * (i + 1) / (n_frames + 1) for i in range(n_frames)]
        for i, t in enumerate(time_points):
            out = str(work / f"frame_{i:02d}_{t:.1f}s.jpg")
            cmd = [
                "ffmpeg", "-y", "-ss", str(t), "-i", config.video,
                "-frames:v", "1", "-q:v", "2", out,
            ]
            try:
                subprocess.run(cmd, capture_output=True, timeout=30)
                if os.path.exists(out) and os.path.getsize(out) > 0:
                    frames.append(FrameInfo(path=out, timestamp=t, direction=f"t{i}", yaw=0))
            except Exception as e:
                log(f"  WARNING: Failed to extract frame at {t:.1f}s: {e}")

    log(f"  -> Extracted {len(frames)} candidate frames")
    if not frames:
        log("ERROR: No frames extracted from video!")
        sys.exit(1)
    return frames


# ──────────────────────────────────────────────
# Step 2: AI Frame Selection
# ──────────────────────────────────────────────

def make_contact_sheet(frames: list[FrameInfo], output_path: str, cols: int = 6) -> str:
    """Create a contact sheet (grid) from frames for quick VL screening."""
    from PIL import Image

    thumb_w, thumb_h = 320, 180
    rows = math.ceil(len(frames) / cols)

    sheet = Image.new("RGB", (cols * thumb_w, rows * thumb_h), (30, 30, 30))
    for i, fr in enumerate(frames):
        try:
            img = Image.open(fr.path)
            img = img.resize((thumb_w, thumb_h), Image.LANCZOS)
            r, c = divmod(i, cols)
            sheet.paste(img, (c * thumb_w, r * thumb_h))
        except Exception:
            pass

    sheet.save(output_path, quality=85)
    return output_path


def _score_all_frames(frames: list[FrameInfo], config: Config) -> list[tuple[FrameInfo, float]]:
    """Two-round AI scoring: contact sheet coarse → single-frame fine.
    Returns ALL scored frames sorted by score descending.
    """
    log("Step 2: AI scoring candidate frames...")

    # ── Round 1: Contact sheet coarse screening ──
    sheet_path = str(Path(config.work_dir) / "contact_sheet.jpg")
    make_contact_sheet(frames, sheet_path)

    coarse_sys = (
        "You are an expert cinematographer and composition analyst. "
        "You will see a contact sheet of frames extracted from a 360° panoramic video."
    )
    coarse_prompt = (
        f"This contact sheet shows {len(frames)} frames arranged in a grid "
        f"(left-to-right, top-to-bottom, numbered 0 to {len(frames)-1}).\n\n"
        f"Select the TOP {config.top_n} frames that would make the best cinematic backdrop "
        f"for placing a person. Evaluate: composition, depth, color harmony, lighting quality, "
        f"and how naturally a person could be placed in the scene.\n\n"
        f"Return ONLY a JSON array of frame indices, e.g. [2, 5, 7, 11, 14, 16]"
    )

    resp = call_vl(config, [sheet_path], coarse_sys, coarse_prompt)
    indices = []
    m = re.search(r"\[[\d\s,]+\]", resp)
    if m:
        try:
            indices = json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    if not indices:
        step = max(1, len(frames) // config.top_n)
        indices = list(range(0, len(frames), step))[:config.top_n]

    shortlist = [frames[i] for i in indices if i < len(frames)]
    if not shortlist:
        shortlist = frames[:config.top_n]
    log(f"  -> Coarse screening: {len(shortlist)} frames shortlisted")

    # ── Round 2: Single-frame fine scoring (parallel) ──
    fine_sys = (
        "You are a world-class cinematographer scoring a landscape frame for use as "
        "a cinematic backdrop where a person will be composited into the scene."
    )

    def score_frame(fr: FrameInfo) -> tuple[FrameInfo, float]:
        prompt = (
            "Score this landscape frame from 0 to 100 on these criteria:\n"
            "1. Cinematic quality (composition, rule of thirds, leading lines)\n"
            "2. Depth and dimension (foreground/middle/background separation)\n"
            "3. Color harmony and visual appeal\n"
            "4. Lighting quality and atmosphere\n"
            "5. Human placement potential (natural spot for a person)\n\n"
            "Return ONLY a JSON object: {\"score\": <0-100>, \"reason\": \"<one sentence>\"}"
        )
        try:
            r = call_vl(config, [fr.path], fine_sys, prompt)
            d = parse_json_from_response(r)
            return fr, float(d.get("score", 50))
        except Exception:
            return fr, 50.0

    scored: list[tuple[FrameInfo, float]] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(score_frame, fr): fr for fr in shortlist}
        for future in as_completed(futures):
            scored.append(future.result())

    scored.sort(key=lambda x: x[1], reverse=True)
    for fr, sc in scored:
        log(f"    {Path(fr.path).name}: {sc:.0f}/100")
    return scored


def select_best_frame(frames: list[FrameInfo], config: Config) -> FrameInfo:
    """Select the single best frame (backward compatible)."""
    scored = _score_all_frames(frames, config)
    best = scored[0]
    log(f"  -> Best frame: {Path(best[0].path).name} (score: {best[1]:.0f}/100)")
    return best[0]


def _compute_diversity(candidate: FrameInfo, selected: list[FrameInfo]) -> float:
    """Compute 0-1 diversity score: how different is candidate from already-selected frames."""
    if not selected:
        return 1.0

    is_360 = any(f.yaw != 0 for f in selected) or candidate.yaw != 0

    if is_360:
        # Angular diversity: min angular distance to any selected frame
        min_dist = min(
            min(abs(candidate.yaw - s.yaw), 360 - abs(candidate.yaw - s.yaw))
            for s in selected
        )
        return min(min_dist / 120.0, 1.0)  # 120°+ = full diversity
    else:
        # Temporal diversity
        timestamps = [s.timestamp for s in selected]
        total_span = max(timestamps) - min(timestamps)
        if total_span == 0:
            total_span = max(candidate.timestamp, 1.0)
        min_dist = min(abs(candidate.timestamp - t) for t in timestamps)
        return min(min_dist / (total_span * 0.5 + 0.1), 1.0)


def select_diverse_frames(frames: list[FrameInfo], config: Config) -> list[FrameInfo]:
    """Select N diverse, high-quality frames for multi-shot video.

    Greedy algorithm: pick highest-score first, then maximize
    effective_score = quality * (0.4 + 0.6 * diversity).
    """
    n_shots = config.shots
    scored = _score_all_frames(frames, config)

    # Filter out very low quality frames
    viable = [(f, s) for f, s in scored if s >= 30]
    if len(viable) < n_shots:
        log(f"  WARNING: Only {len(viable)} viable frames for {n_shots} shots")
        if not viable:
            viable = scored[:1]
        n_shots = min(n_shots, len(viable))

    # Greedy selection: quality * diversity
    selected: list[tuple[FrameInfo, float]] = []
    remaining = list(viable)

    # First frame: highest quality
    selected.append(remaining.pop(0))

    for _ in range(n_shots - 1):
        if not remaining:
            break
        best_idx, best_effective = 0, -1.0
        for i, (frame, score) in enumerate(remaining):
            diversity = _compute_diversity(frame, [s[0] for s in selected])
            effective = score * (0.4 + 0.6 * diversity)
            if effective > best_effective:
                best_effective = effective
                best_idx = i
        selected.append(remaining.pop(best_idx))

    # Sort by narrative order: yaw (360°) or timestamp (normal)
    is_360 = any(f.yaw != 0 for f, _ in selected)
    if is_360:
        selected.sort(key=lambda x: x[0].yaw)
    else:
        selected.sort(key=lambda x: x[0].timestamp)

    result = [f for f, _ in selected]
    log(f"  -> Selected {len(result)} diverse frames for {config.shots} shots:")
    for fr in result:
        sc = next(s for f, s in scored if f.path == fr.path)
        log(f"    {Path(fr.path).name} (score: {sc:.0f}, yaw: {fr.yaw}°, t: {fr.timestamp:.1f}s)")
    return result


# ──────────────────────────────────────────────
# Step 3: Scene Analysis
# ──────────────────────────────────────────────

def analyze_scene(frame: FrameInfo, config: Config) -> SceneAnalysis:
    """Deep scene analysis with VL model."""
    log("Step 3: Deep analyzing scene...")

    sys_prompt = (
        "You are a film location scout and visual storytelling expert. "
        "Analyze this landscape image in detail for cinematic video production."
    )
    user_prompt = (
        "Analyze this landscape image and return a JSON object with:\n"
        "{\n"
        '  "scene_type": "<one word: mountain/lake/beach/ocean/forest/desert/city/field/snow/sunset/waterfall/temple/garden/bridge/river/other>",\n'
        '  "time_of_day": "<golden_hour/blue_hour/midday/overcast/night/sunrise>",\n'
        '  "weather": "<clear/cloudy/partly_cloudy/foggy/rainy/snowy>",\n'
        '  "dominant_colors": ["<color1>", "<color2>", "<color3>"],\n'
        '  "key_elements": ["<element1>", "<element2>", "<element3>"],\n'
        '  "mood": "<one or two words describing the emotional atmosphere>",\n'
        '  "depth_layers": ["foreground: <what>", "middle: <what>", "background: <what>"],\n'
        '  "suggested_person_position": "<where a person would naturally stand/sit>",\n'
        '  "cinematic_description": "<A rich 2-3 sentence visual description of the scene, written like a screenplay direction, vivid and atmospheric>"\n'
        "}\n\n"
        "Return ONLY the JSON, no other text."
    )

    resp = call_vl(config, [frame.path], sys_prompt, user_prompt)
    d = parse_json_from_response(resp)

    scene = SceneAnalysis(
        scene_type=d.get("scene_type", "landscape"),
        time_of_day=d.get("time_of_day", "golden_hour"),
        weather=d.get("weather", "clear"),
        dominant_colors=d.get("dominant_colors", []),
        key_elements=d.get("key_elements", []),
        mood=d.get("mood", "serene"),
        depth_layers=d.get("depth_layers", []),
        suggested_person_position=d.get("suggested_person_position", "standing in the center"),
        cinematic_description=d.get("cinematic_description", "A breathtaking landscape stretches out in every direction."),
    )

    log(f"  -> Scene: {scene.scene_type} | Time: {scene.time_of_day} | Mood: {scene.mood}")
    return scene


# ──────────────────────────────────────────────
# Step 4: Person Analysis
# ──────────────────────────────────────────────

def analyze_person(config: Config) -> PersonAnalysis:
    """Analyze selfie for person features."""
    log("Step 4: Analyzing person features...")

    sys_prompt = (
        "You are a portrait photography expert. Analyze this selfie to describe "
        "the person's appearance for use in a cinematic video generation prompt. "
        "IMPORTANT: Always describe clothing in a modest, safe-for-work manner. "
        "If shoulders or skin are visible, describe the garment (e.g. 'off-shoulder top', "
        "'sleeveless blouse', 'tank top') rather than describing bare skin."
    )
    user_prompt = (
        "Describe this person's visual appearance for a video generation AI. Return a JSON:\n"
        "{\n"
        '  "appearance": "<brief physical description: gender, approximate age, build>",\n'
        '  "clothing": "<what they are wearing, colors, style. MUST describe as a garment name, e.g. white off-shoulder top, sleeveless blouse. Never say bare/naked/no clothing>",\n'
        '  "hair": "<hair style, color, length, be concise: e.g. short wavy dark hair>",\n'
        '  "expression": "<facial expression, be concise: e.g. gentle smile>",\n'
        '  "style_vibe": "<overall style: casual/formal/sporty/elegant/bohemian/etc>"\n'
        "}\n\n"
        "Be very concise and use short phrases. Return ONLY the JSON."
    )

    resp = call_vl(config, [config.selfie], sys_prompt, user_prompt)
    d = parse_json_from_response(resp)

    person = PersonAnalysis(
        appearance=d.get("appearance", "a person"),
        clothing=d.get("clothing", "casual clothes"),
        hair=d.get("hair", ""),
        expression=d.get("expression", "natural expression"),
        style_vibe=d.get("style_vibe", "casual"),
    )

    log(f"  -> Person: {person.appearance[:50]}...")
    return person


# ──────────────────────────────────────────────
# Step 5: Prompt Engineering (v2 — simplified)
# ──────────────────────────────────────────────

# Words that may trigger content moderation when describing clothing
_UNSAFE_CLOTHING_WORDS = [
    "bare", "naked", "nude", "no visible", "no top", "no shirt",
    "no clothing", "no clothes", "topless", "shirtless", "undressed",
    "exposed", "revealing", "see-through", "transparent",
]

def sanitize_clothing(clothing: str) -> str:
    """Sanitize clothing description to avoid content moderation triggers."""
    lower = clothing.lower()
    for word in _UNSAFE_CLOTHING_WORDS:
        if word in lower:
            return "casual light-colored top"
    if not clothing.strip() or clothing.strip().lower() in ("none", "n/a", "unknown"):
        return "casual outfit"
    return clothing


def get_lighting_desc(scene: SceneAnalysis, frame: FrameInfo) -> str:
    """Adaptive lighting description based on time of day and direction."""
    tod = scene.time_of_day
    direction = frame.direction

    dir_hint = None
    if direction in ("front",):
        dir_hint = "front"
    elif direction in ("back",):
        dir_hint = "back"
    elif direction in ("left", "right", "up_left", "up_right"):
        dir_hint = "side"

    key = (tod, dir_hint)
    if key in LIGHTING_ADAPTATION:
        return LIGHTING_ADAPTATION[key]
    key = (tod, None)
    if key in LIGHTING_ADAPTATION:
        return LIGHTING_ADAPTATION[key]
    return "beautiful natural lighting"


def get_scene_action(scene: SceneAnalysis, style: dict) -> str:
    """Get natural action for person based on scene type."""
    # Scene-specific action first (low-motion)
    for keyword, scene_action in SCENE_ACTION_MAP.items():
        if keyword in scene.scene_type.lower():
            return scene_action
    # Fallback to style default
    return style.get("action", "standing still, looking at the scenery")


def build_negative_prompt(style_key: str) -> str:
    """Build full negative prompt: base + style-specific."""
    style = STYLE_PRESETS.get(style_key, STYLE_PRESETS["cinematic"])
    style_neg = style.get("negative", "")
    if style_neg:
        return f"{NEGATIVE_PROMPT_BASE}, {style_neg}"
    return NEGATIVE_PROMPT_BASE


def build_r2v_prompt(scene: SceneAnalysis, person: PersonAnalysis,
                     frame: FrameInfo, config: Config) -> str:
    """Build a concise R2V prompt (target: 200-300 chars).

    Structure: character1 [action], [clothing], [hair].
               [scene_type] with [key_elements].
               [lighting], [camera].
               [identity keywords]
    """
    log(f"Step 5: Building fusion prompt ({config.style} style)...")

    style = STYLE_PRESETS.get(config.style, STYLE_PRESETS["cinematic"])
    lighting = get_lighting_desc(scene, frame)
    action = get_scene_action(scene, style)

    # Person description (concise) — with content safety sanitization
    clothing = sanitize_clothing(person.clothing)
    person_desc = f"wearing {clothing}"
    if person.hair:
        person_desc += f", {person.hair}"

    # Scene context (concise — just key elements, no depth layers or cinematic prose)
    elements = ", ".join(scene.key_elements[:3]) if scene.key_elements else "beautiful scenery"

    # Compose the prompt — short keyword phrases, NOT prose
    parts = [
        f"character1 {action}",
        person_desc,
        f"{scene.scene_type} with {elements}",
        lighting,
        style["camera"],
        IDENTITY_SUFFIX,
    ]

    prompt = ". ".join(part.strip().rstrip(".") for part in parts if part.strip())

    # Hard limit at 500 chars (generous, but prompt should naturally be ~250)
    if len(prompt) > 500:
        prompt = prompt[:497] + "..."

    log(f"  -> Prompt ({len(prompt)} chars): {prompt[:120]}...")

    # Save prompt for debugging
    prompt_file = Path(config.work_dir) / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    return prompt


# ──────────────────────────────────────────────
# Step 6: Generation — R2V Video
# ──────────────────────────────────────────────

def generate_r2v_video(prompt: str, config: Config, label: str = "video") -> str:
    """Generate video using Wan2.6 R2V via bailian-multimodal-skills."""
    log(f"  Generating {label}: {config.model}, size={config.size}, duration={config.duration}s...")

    bailian_dir = find_bailian_dir(config.bailian_dir)
    script = os.path.join(bailian_dir, "scripts", "run_multimodal.py")

    negative = build_negative_prompt(config.style)

    cmd = [
        "uv", "run", "--directory", bailian_dir, "python", script,
        "--mode", "r2v",
        "--model", config.model,
        "--prompt", prompt,
        "--reference-urls", os.path.abspath(config.selfie),
        "--size", config.size,
        "--duration", str(config.duration),
        "--output", os.path.abspath(config.output),
        "--no-audio",
        "--negative-prompt", negative,
    ]
    if config.api_key:
        cmd.extend(["--api-key", config.api_key])

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        for line in proc.stderr:
            line = line.strip()
            if line:
                log(f"    [R2V] {line}")
        proc.wait()

        stdout = proc.stdout.read() if proc.stdout else ""
        if stdout.strip():
            for line in stdout.strip().split("\n"):
                if line.startswith("MEDIA:"):
                    log(f"    -> R2V output: {line.replace('MEDIA:', '').strip()}")
                print(line)

        if proc.returncode != 0:
            log(f"ERROR: R2V process exited with code {proc.returncode}")
            sys.exit(1)

    except Exception as e:
        log(f"ERROR: R2V generation failed: {e}")
        sys.exit(1)

    if os.path.exists(config.output) and os.path.getsize(config.output) > 0:
        size_mb = os.path.getsize(config.output) / (1024 * 1024)
        log(f"    -> {label} generated: {config.output} ({size_mb:.1f} MB)")
    else:
        log(f"ERROR: Output not found: {config.output}")
        sys.exit(1)

    return config.output


# ──────────────────────────────────────────────
# Step 6b-6e: Additional outputs
# ──────────────────────────────────────────────

def generate_gif(video_path: str, output_path: str, fps: int = 12, width: int = 480):
    """Convert video to high-quality GIF using ffmpeg palette method."""
    log(f"  Generating GIF: fps={fps}, width={width}...")
    palette = output_path.replace(".gif", "_palette.png")

    # Step 1: Generate palette
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,palettegen",
         palette],
        capture_output=True, timeout=60,
    )
    # Step 2: Apply palette
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-i", palette,
         "-lavfi", f"fps={fps},scale={width}:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3",
         output_path],
        capture_output=True, timeout=60,
    )
    # Cleanup palette
    if os.path.exists(palette):
        os.remove(palette)

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log(f"    -> GIF: {output_path} ({size_mb:.1f} MB)")
        print(f"MEDIA: {os.path.abspath(output_path)}")
    else:
        log(f"  WARNING: GIF generation failed")


def generate_vertical(prompt: str, config: Config) -> str:
    """Generate vertical (9:16) video via R2V."""
    out = get_output_path(config, "_vertical", ".mp4")
    vert_config = replace(config, size="720*1280", output=out)
    return generate_r2v_video(prompt, vert_config, label="vertical video (9:16)")


def extract_cover(video_path: str, output_path: str):
    """Extract the best frame from video as cover image."""
    log(f"  Extracting cover image...")
    duration = get_video_duration(video_path)
    mid = duration / 2

    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(mid), "-i", video_path,
         "-frames:v", "1", "-q:v", "2", output_path],
        capture_output=True, timeout=30,
    )

    if os.path.exists(output_path):
        log(f"    -> Cover: {output_path}")
        print(f"MEDIA: {os.path.abspath(output_path)}")
    else:
        log(f"  WARNING: Cover extraction failed")


def generate_captioned(video_path: str, caption: str, output_path: str, font_path: str = ""):
    """Overlay text caption on video using Pillow + ffmpeg."""
    log(f"  Generating captioned video: \"{caption}\"...")
    from PIL import Image, ImageDraw

    w, h = get_video_dimensions(video_path)
    if w == 0 or h == 0:
        log("  WARNING: Cannot detect video dimensions, skipping caption")
        return

    # Create transparent overlay with text
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_size = max(24, int(h * 0.05))
    font = load_cjk_font(size=font_size, font_path=font_path)

    # Semi-transparent dark strip behind text
    strip_y = int(h * 0.85)
    strip_h = int(h * 0.10)
    draw.rectangle(
        [(0, strip_y), (w, strip_y + strip_h)],
        fill=(0, 0, 0, 100),
    )

    # Draw text centered on the strip
    text_y = strip_y + strip_h // 2
    draw.text(
        (w // 2, text_y), caption, font=font,
        fill=(255, 255, 255, 220), anchor="mm",
    )

    overlay_path = video_path.replace(".mp4", "_overlay.png")
    overlay.save(overlay_path)

    # ffmpeg overlay
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-i", overlay_path,
         "-filter_complex", "overlay=0:0",
         "-codec:v", "libx264", "-preset", "fast", "-crf", "18",
         output_path],
        capture_output=True, timeout=120,
    )

    # Cleanup
    if os.path.exists(overlay_path):
        os.remove(overlay_path)

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log(f"    -> Captioned: {output_path} ({size_mb:.1f} MB)")
        print(f"MEDIA: {os.path.abspath(output_path)}")
    else:
        log(f"  WARNING: Captioned video generation failed")


# ──────────────────────────────────────────────
# Multi-shot: Video concatenation with transitions
# ──────────────────────────────────────────────

def concat_videos_with_transition(
    video_paths: list[str],
    transition: str,
    transition_duration: float,
    output_path: str,
) -> str:
    """Concatenate multiple video clips with ffmpeg xfade transitions."""
    import shutil

    n = len(video_paths)
    if n == 0:
        log("ERROR: No video clips to concatenate")
        sys.exit(1)
    if n == 1:
        shutil.copy2(video_paths[0], output_path)
        return output_path

    log(f"Step 7: Concatenating {n} shots with {transition} transition...")

    xfade_type = "fade" if transition == "crossfade" else "fadeblack"
    td = transition_duration

    # Get duration of each clip
    durations = [get_video_duration(p) for p in video_paths]
    log(f"  Clip durations: {', '.join(f'{d:.1f}s' for d in durations)}")

    # Build ffmpeg filter_complex chain
    # For N clips, we need N-1 xfade operations
    inputs: list[str] = []
    for p in video_paths:
        inputs.extend(["-i", p])

    filter_parts: list[str] = []
    cumulative_duration = durations[0]

    # First xfade: [0:v][1:v] → [v01]
    offset0 = cumulative_duration - td
    filter_parts.append(
        f"[0:v][1:v]xfade=transition={xfade_type}:duration={td}:offset={offset0:.3f}[v01]"
    )
    cumulative_duration += durations[1] - td

    # Subsequent xfades
    for i in range(2, n):
        prev_label = f"v{i-2:02d}{i-1:02d}" if i == 2 else f"v{i-1}"
        if i == 2:
            prev_label = "v01"
        out_label = f"v{i}" if i < n - 1 else "vout"

        offset = cumulative_duration - td
        filter_parts.append(
            f"[{prev_label}][{i}:v]xfade=transition={xfade_type}:duration={td}:offset={offset:.3f}[{out_label}]"
        )
        cumulative_duration += durations[i] - td

    final_label = "vout" if n > 2 else "v01"
    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", f"[{final_label}]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            log(f"  WARNING: xfade concat failed, falling back to simple concat")
            log(f"    stderr: {result.stderr[:300]}")
            return _concat_simple(video_paths, output_path)
    except subprocess.TimeoutExpired:
        log("  WARNING: Concat timed out, falling back to simple concat")
        return _concat_simple(video_paths, output_path)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        total_duration = sum(durations) - (n - 1) * td
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log(f"  -> Concatenated: {output_path} ({size_mb:.1f} MB, ~{total_duration:.0f}s)")
    else:
        log("  WARNING: Concat produced no output, falling back")
        return _concat_simple(video_paths, output_path)

    return output_path


def _concat_simple(video_paths: list[str], output_path: str) -> str:
    """Simple concat fallback (no transitions) using ffmpeg concat demuxer."""
    concat_list = output_path + ".txt"
    with open(concat_list, "w") as f:
        for p in video_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c", "copy", output_path],
        capture_output=True, timeout=60,
    )

    if os.path.exists(concat_list):
        os.remove(concat_list)

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log(f"  -> Simple concat: {output_path} ({size_mb:.1f} MB)")
    return output_path

def save_analysis(work_dir: str, scene: SceneAnalysis, person: PersonAnalysis,
                  best_frame: FrameInfo, prompt: str):
    """Save analysis results to JSON for resumability."""
    data = {
        "scene": asdict(scene),
        "person": asdict(person),
        "best_frame": asdict(best_frame),
        "prompt": prompt,
    }
    out = Path(work_dir) / "analysis.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  -> Analysis saved to {out}")


def load_analysis(work_dir: str) -> tuple[SceneAnalysis, PersonAnalysis, FrameInfo, str]:
    """Load previous analysis results."""
    path = Path(work_dir) / "analysis.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    scene = SceneAnalysis(**data["scene"])
    person = PersonAnalysis(**data["person"])
    frame = FrameInfo(**data["best_frame"])
    prompt = data.get("prompt", "")
    return scene, person, frame, prompt


def run_pipeline(config: Config):
    """Full pipeline with i2v/r2v engine split + multi-shot support."""
    outputs = config.outputs
    if "all" in outputs:
        outputs = ALL_OUTPUTS

    # Multi-shot + vertical is too expensive (2x I2V calls), skip vertical
    if config.shots > 1 and "vertical" in outputs:
        log("  NOTE: Vertical output skipped in multi-shot mode (use single-shot for vertical)")
        outputs = [o for o in outputs if o != "vertical"]

    log(f"Starting TimeSlice Fusion v3 pipeline...")
    log(f"  Engine:  {config.engine}")
    log(f"  Video:   {config.video}")
    log(f"  Selfie:  {config.selfie}")
    log(f"  Style:   {config.style}")
    log(f"  Model:   {config.model}")
    log(f"  Shots:   {config.shots}")
    log(f"  Outputs: {', '.join(outputs)}")
    if config.engine == "i2v":
        log(f"  Composite: {config.composite_style}")
    if config.shots > 1:
        log(f"  Transition: {config.transition} ({config.transition_duration}s)")
    if config.caption:
        log(f"  Caption: {config.caption}")
    log("")

    start = time.time()

    # Ensure output directory exists
    out_base = config.output
    if len(outputs) > 1 or os.path.isdir(out_base):
        os.makedirs(out_base, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(out_base)) or ".", exist_ok=True)

    # Step 0: Preprocess selfie
    if config.engine == "r2v":
        processed_selfie = preprocess_selfie(config)
    else:
        log("Step 0: Using full selfie for I2V (no head-shoulder crop)...")
        processed_selfie = config.selfie
    config = replace(config, selfie=processed_selfie)

    # Step 0b: Remove background (I2V only)
    cutout_path = None
    if config.engine == "i2v":
        cutout_path = remove_background(processed_selfie, config.work_dir)

    # Step 1: Extract frames
    frames = extract_360_frames(config)

    # Step 2: Select frames (single or multi-shot)
    if config.shots > 1:
        selected = select_diverse_frames(frames, config)
    else:
        selected = [select_best_frame(frames, config)]

    # Step 3 + 4: Analyze scenes (N, parallel) + person (1)
    n_workers = min(len(selected) + 1, 5)
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        person_future = pool.submit(analyze_person, config)
        scene_futures = [pool.submit(analyze_scene, fr, config) for fr in selected]
        person = person_future.result()
        scenes = [f.result() for f in scene_futures]

    # ── Engine split ──
    if config.engine == "i2v":
        prompt_info = _run_i2v_steps(selected, cutout_path, scenes, person, outputs, config)
    else:
        prompt_info = _run_r2v_steps(selected[0], scenes[0], person, outputs, config)

    save_analysis(config.work_dir, scenes[0], person, selected[0], prompt_info)

    elapsed = time.time() - start
    log("")
    log(f"Done! Total time: {elapsed:.0f}s")


def _run_i2v_steps(selected_frames: list[FrameInfo], cutout_path: str,
                   scenes: list[SceneAnalysis], person: PersonAnalysis,
                   outputs: list[str], config: Config) -> str:
    """I2V pipeline: per-shot composite + I2V, then concat if multi-shot."""
    import shutil

    n_shots = len(selected_frames)
    shot_videos: list[str] = []
    last_motion_prompt = ""

    # Generate each shot
    for i, (frame, scene) in enumerate(zip(selected_frames, scenes)):
        if n_shots > 1:
            log(f"\n{'='*50}")
            log(f"Shot {i+1}/{n_shots}: {Path(frame.path).name}")
            log(f"{'='*50}")

        # Step 5: Build I2V motion prompt (per-shot, scene may differ)
        motion_prompt, negative_prompt = build_i2v_prompt(scene, config)
        last_motion_prompt = motion_prompt

        # Step 5b: Composite person onto this frame
        composite_path = composite_person(
            frame.path, cutout_path, scene, config, shot_index=i,
        )

        # Step 6: I2V generation (sequential — DashScope rate limit)
        shot_out = str(Path(config.work_dir) / f"shot_{i:02d}.mp4")
        shot_config = replace(config, output=shot_out)

        try:
            log(f"Step 6: Generating I2V shot {i+1}/{n_shots}...")
            video_path = generate_i2v_video(
                composite_path, motion_prompt, negative_prompt,
                shot_config, label=f"shot {i+1}/{n_shots}",
            )
            shot_videos.append(video_path)
        except SystemExit:
            log(f"  WARNING: Shot {i+1} generation failed, skipping")
            continue

    if not shot_videos:
        log("ERROR: All shots failed!")
        sys.exit(1)

    # Step 7: Concat or copy to final output
    generated: list[tuple[str, str]] = []
    video_path = None

    need_video = any(o in outputs for o in ("video", "gif", "cover", "captioned"))
    if need_video:
        final_out = get_output_path(config, "", ".mp4")
        if len(shot_videos) == 1:
            shutil.copy2(shot_videos[0], final_out)
        else:
            concat_videos_with_transition(
                shot_videos, config.transition,
                config.transition_duration, final_out,
            )
        video_path = final_out
        if os.path.exists(final_out):
            size_mb = os.path.getsize(final_out) / (1024 * 1024)
            log(f"  -> Final video: {final_out} ({size_mb:.1f} MB)")
            print(f"MEDIA: {os.path.abspath(final_out)}")
        generated.append(("video", final_out))

    # Vertical video (single-shot only, skipped in multi-shot by run_pipeline)
    if "vertical" in outputs and len(selected_frames) == 1:
        from PIL import Image
        bg = Image.open(selected_frames[0].path).convert("RGB")
        bg_vert = _crop_to_vertical(bg)
        vert_bg_path = str(Path(config.work_dir) / "vertical_bg.jpg")
        bg_vert.save(vert_bg_path, "JPEG", quality=95)

        vert_composite = composite_person(vert_bg_path, cutout_path, scenes[0],
                                          replace(config, composite_style="natural"),
                                          shot_index=99)
        vert_out = get_output_path(config, "_vertical", ".mp4")
        vert_config = replace(config, output=vert_out)
        vert_path = generate_i2v_video(
            vert_composite, last_motion_prompt, "",
            vert_config, label="vertical video (9:16)", resolution="720P",
        )
        generated.append(("vertical", vert_path))

    # Post-processing on final video
    _run_post_processing(video_path, outputs, config, generated)

    log(f"Generated {len(generated)} output(s):")
    for kind, path in generated:
        log(f"  [{kind}] {path}")

    return last_motion_prompt


def _run_r2v_steps(best: FrameInfo, scene: SceneAnalysis,
                   person: PersonAnalysis, outputs: list[str], config: Config) -> str:
    """R2V pipeline (legacy): prompt → R2V generate."""
    # Step 5: Build R2V prompt
    prompt = build_r2v_prompt(scene, person, best, config)

    # Step 6: Generate outputs
    log("Step 6: Generating R2V outputs...")
    video_path = None
    generated = []

    need_video = any(o in outputs for o in ("video", "gif", "cover", "captioned"))
    if need_video:
        video_out = get_output_path(config, "", ".mp4")
        video_config = replace(config, output=video_out)
        video_path = generate_r2v_video(prompt, video_config, label="main video")
        generated.append(("video", video_path))

    if "vertical" in outputs:
        vert_out = get_output_path(config, "_vertical", ".mp4")
        vert_config = replace(config, size="720*1280", output=vert_out)
        vert_path = generate_r2v_video(prompt, vert_config, label="vertical video (9:16)")
        generated.append(("vertical", vert_path))

    _run_post_processing(video_path, outputs, config, generated)

    log(f"Generated {len(generated)} output(s):")
    for kind, path in generated:
        log(f"  [{kind}] {path}")

    return prompt


def _run_post_processing(video_path: str | None, outputs: list[str],
                         config: Config, generated: list):
    """Shared post-processing: GIF, cover, captioned."""
    if "gif" in outputs and video_path:
        gif_out = get_output_path(config, "", ".gif")
        generate_gif(video_path, gif_out, fps=config.gif_fps, width=config.gif_width)
        generated.append(("gif", gif_out))

    if "cover" in outputs and video_path:
        cover_out = get_output_path(config, "_cover", ".jpg")
        extract_cover(video_path, cover_out)
        generated.append(("cover", cover_out))

    if "captioned" in outputs and video_path and config.caption:
        cap_out = get_output_path(config, "_captioned", ".mp4")
        generate_captioned(video_path, config.caption, cap_out, font_path=config.font)
        generated.append(("captioned", cap_out))
    elif "captioned" in outputs and not config.caption:
        log("  WARNING: --caption not set, skipping captioned output")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TimeSlice Fusion v3 — Fuse 360° video + selfie into cinematic video",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Sub-commands")

    # --- run (full pipeline) ---
    p_run = sub.add_parser("run", help="Run the full pipeline")
    p_run.add_argument("--video", required=True, help="360° video path")
    p_run.add_argument("--selfie", required=True, help="Selfie photo path")
    p_run.add_argument("--output", default="./timeslice_output.mp4", help="Output path (file or directory)")
    p_run.add_argument("--style", default="cinematic", choices=list(STYLE_PRESETS.keys()))
    p_run.add_argument("--model", default=None, help="Model name (auto: wan2.6-i2v or wan2.6-r2v)")
    p_run.add_argument("--vl-model", default="qwen-vl-max", help="Vision model for analysis")
    p_run.add_argument("--duration", type=int, default=5, help="Output video duration (seconds)")
    p_run.add_argument("--size", default="1280*720", help="Output resolution (R2V only)")
    p_run.add_argument("--top-n", type=int, default=6, help="Coarse screening top-N")
    p_run.add_argument("--api-key", help="DashScope API key")
    p_run.add_argument("--bailian-dir", help="bailian-multimodal-skills directory")
    p_run.add_argument("--work-dir", help="Working directory for intermediate files")
    # v2: multi-output
    p_run.add_argument("--outputs", nargs="+", default=["video"],
                       choices=ALL_OUTPUTS + ["all"],
                       help="Output types: video gif vertical cover captioned all")
    p_run.add_argument("--caption", default="", help="Text caption for captioned output")
    p_run.add_argument("--gif-fps", type=int, default=12, help="GIF frame rate")
    p_run.add_argument("--gif-width", type=int, default=480, help="GIF width in pixels")
    p_run.add_argument("--font", default="", help="Font file path for text overlay")
    # v3: engine + composite
    p_run.add_argument("--engine", default="i2v", choices=["i2v", "r2v"],
                       help="Generation engine: i2v (real scenery, default) or r2v (AI generation)")
    p_run.add_argument("--composite-style", default="natural", choices=["natural", "collage"],
                       help="Person compositing style (I2V only): natural blend or artistic collage")
    # v3.1: multi-shot
    p_run.add_argument("--shots", type=int, default=1,
                       help="Number of shots (default 1, use 3 for multi-shot)")
    p_run.add_argument("--transition", default="crossfade",
                       choices=["crossfade", "fade_to_black"],
                       help="Transition style between shots (multi-shot only)")

    # --- extract-frames ---
    p_extract = sub.add_parser("extract-frames", help="Extract frames only")
    p_extract.add_argument("--video", required=True, help="360° video path")
    p_extract.add_argument("--work-dir", help="Output directory for frames")

    # --- analyze ---
    p_analyze = sub.add_parser("analyze", help="Analyze frames + selfie only")
    p_analyze.add_argument("--work-dir", required=True, help="Working directory with frames")
    p_analyze.add_argument("--selfie", required=True, help="Selfie photo path")
    p_analyze.add_argument("--style", default="cinematic", choices=list(STYLE_PRESETS.keys()))
    p_analyze.add_argument("--vl-model", default="qwen-vl-max", help="Vision model")
    p_analyze.add_argument("--top-n", type=int, default=6)
    p_analyze.add_argument("--api-key", help="DashScope API key")

    # --- generate ---
    p_gen = sub.add_parser("generate", help="Generate video from analysis")
    p_gen.add_argument("--work-dir", required=True, help="Working directory with analysis.json")
    p_gen.add_argument("--selfie", required=True, help="Selfie photo path")
    p_gen.add_argument("--output", default="./timeslice_output.mp4", help="Output video path")
    p_gen.add_argument("--model", default="wan2.6-r2v", help="R2V model")
    p_gen.add_argument("--duration", type=int, default=5)
    p_gen.add_argument("--size", default="1280*720")
    p_gen.add_argument("--api-key", help="DashScope API key")
    p_gen.add_argument("--bailian-dir", help="bailian-multimodal-skills directory")
    p_gen.add_argument("--style", default="cinematic", choices=list(STYLE_PRESETS.keys()))

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Resolve work directory
    work_dir = getattr(args, "work_dir", None)
    if not work_dir:
        work_dir = tempfile.mkdtemp(prefix="timeslice_")
        log(f"Using temp work dir: {work_dir}")

    if args.command == "run":
        if not os.path.exists(args.video):
            log(f"ERROR: Video not found: {args.video}")
            sys.exit(1)
        if not os.path.exists(args.selfie):
            log(f"ERROR: Selfie not found: {args.selfie}")
            sys.exit(1)

        config = Config(
            video=os.path.abspath(args.video),
            selfie=os.path.abspath(args.selfie),
            output=os.path.abspath(args.output),
            style=args.style,
            model=args.model or "",
            vl_model=args.vl_model,
            duration=args.duration,
            size=args.size,
            top_n=args.top_n,
            api_key=get_api_key(args.api_key),
            bailian_dir=args.bailian_dir or "",
            work_dir=work_dir,
            outputs=args.outputs,
            caption=args.caption,
            gif_fps=args.gif_fps,
            gif_width=args.gif_width,
            font=args.font,
            engine=args.engine,
            composite_style=args.composite_style,
            shots=args.shots,
            transition=args.transition,
        )
        run_pipeline(config)

    elif args.command == "extract-frames":
        if not os.path.exists(args.video):
            log(f"ERROR: Video not found: {args.video}")
            sys.exit(1)
        config = Config(video=os.path.abspath(args.video), work_dir=work_dir)
        frames = extract_360_frames(config)
        log(f"Extracted {len(frames)} frames to {work_dir}/frames/")

    elif args.command == "analyze":
        if not os.path.exists(args.selfie):
            log(f"ERROR: Selfie not found: {args.selfie}")
            sys.exit(1)
        config = Config(
            selfie=os.path.abspath(args.selfie),
            style=args.style,
            vl_model=args.vl_model,
            top_n=args.top_n,
            api_key=get_api_key(args.api_key),
            work_dir=args.work_dir,
        )
        frame_dir = Path(args.work_dir) / "frames"
        frames = []
        for f in sorted(frame_dir.glob("*.jpg")):
            frames.append(FrameInfo(path=str(f), timestamp=0, direction=f.stem, yaw=0))
        if not frames:
            log("ERROR: No frames found in work_dir/frames/")
            sys.exit(1)

        best = select_best_frame(frames, config)
        scene = analyze_scene(best, config)
        person = analyze_person(config)
        prompt = build_r2v_prompt(scene, person, best, config)
        save_analysis(args.work_dir, scene, person, best, prompt)

    elif args.command == "generate":
        config = Config(
            selfie=os.path.abspath(args.selfie),
            output=os.path.abspath(args.output),
            model=args.model,
            duration=args.duration,
            size=args.size,
            api_key=get_api_key(args.api_key),
            bailian_dir=args.bailian_dir or "",
            work_dir=args.work_dir,
            style=args.style,
        )
        _, _, _, prompt = load_analysis(args.work_dir)
        generate_r2v_video(prompt, config)


if __name__ == "__main__":
    main()
