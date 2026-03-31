---
name: timeslice-fusion
description: Use when user sends a 360-degree panoramic video and a selfie photo, wanting to fuse them into a cinematic video of the person in the scenery. Triggers include keywords like time slice, panoramic fusion, person in scenery, selfie with landscape video, 360 video portrait, 时空切片, 融合风景, 人景合一, 全景融合.
---

# TimeSlice Fusion — 时空融影 v3

## Overview

将一段 360° 全景风景视频和一张自拍照，通过 AI 融合为一段"你在风景中"的电影级短视频。

**双引擎架构:**
- **I2V (默认)** — 真实帧合成 + I2V 动画化，风景 100% 还原现实场景
- **R2V (备选)** — R2V 参考图生视频，AI 重绘全部内容

**I2V 处理流程:** 自拍抠图(macOS Vision) → 帧提取 → AI选帧 → 场景分析 → 人物分析 → 人物合成 → 运动提示词 → I2V动画化
**R2V 处理流程:** 自拍预处理(头肩裁剪) → 帧提取 → AI选帧 → 场景分析 → 人物分析 → Prompt融合 → R2V生成

### v3.1 多镜头 (Multi-Shot)

- **多镜头拍摄:** `--shots N` 从视频中选取 N 个多样化帧，每帧独立生成 I2V 视频，拼接为"时光切片"短片
- **多样性选帧:** 贪心算法，quality × (0.4 + 0.6 × diversity)，360° 视频按角度分散，普通视频按时间分散
- **转场效果:** `--transition crossfade` (交叉淡入淡出) 或 `fade_to_black` (黑场过渡)，可调时长
- **容错机制:** 某个 shot 生成失败时自动跳过，剩余 shot 仍然拼接输出
- **智能限制:** 多镜头模式下自动跳过竖屏输出 (太贵)

### v3 改进

- **双引擎:** I2V (真实场景还原, 默认) + R2V (AI 重绘, 备选)
- **人像抠图:** macOS Vision VNGeneratePersonSegmentationRequest 发丝级精度
- **人物合成:** 自然融合 (光照适配/阴影/边缘羽化) + 艺术拼贴 (描边/旋转) 两种模式
- **运动提示词:** 仅描述动作 (头转/微风/镜头推移)，不描述场景内容
- **底部渐隐:** 人物底部 20% 区域 alpha 渐变，自然融入背景

### v2 改进

- **质量优化:** 精简 prompt 至 200-300 字符 (原 800+)，增强 50+ 词负面提示词，新增身份一致性关键词
- **自拍预处理:** 自动裁剪头肩构图 (上部 70%)，提升 R2V 面部比例和识别质量
- **低动作策略:** 场景动作降为静态姿势 (站立/凝视)，减少运动失真
- **多产出:** 支持同时输出视频、GIF、竖屏视频 (9:16)、封面图、带字幕视频
- **新增风格:** noir (黑色电影)、vintage (复古胶片)、anime (动漫风)

## When to Use

- 用户发送了一段 360° 全景视频 + 一张自拍照
- 用户希望将自己"放进"风景视频中
- 用户提到"时空切片"、"全景融合"、"人景合一"等关键词
- 用户希望风景保持真实 → 使用 I2V 引擎 (默认)
- 用户希望 AI 完全重绘 → 使用 R2V 引擎

## Quick Start

```bash
# 基础用法 — I2V 引擎 (风景100%还原, 推荐)
uv run ~/.qoderwork/skills/timeslice-fusion/scripts/timeslice.py run \
  --video /path/to/360_video.mp4 \
  --selfie /path/to/selfie.jpg \
  --output /path/to/output.mp4

# I2V + 艺术拼贴合成
uv run ~/.qoderwork/skills/timeslice-fusion/scripts/timeslice.py run \
  --video panorama.mp4 \
  --selfie me.jpg \
  --output result.mp4 \
  --composite-style collage

# R2V 引擎 (AI 重绘全部内容)
uv run ~/.qoderwork/skills/timeslice-fusion/scripts/timeslice.py run \
  --video panorama.mp4 \
  --selfie me.jpg \
  --output result.mp4 \
  --engine r2v

# 指定风格
uv run ~/.qoderwork/skills/timeslice-fusion/scripts/timeslice.py run \
  --video panorama.mp4 \
  --selfie me.jpg \
  --output result.mp4 \
  --style dreamy

# 一次生成全部格式
uv run ~/.qoderwork/skills/timeslice-fusion/scripts/timeslice.py run \
  --video panorama.mp4 \
  --selfie me.jpg \
  --output ./output_dir/ \
  --outputs all \
  --caption "时光切片 · 我在风景中"

# 生成视频 + GIF + 封面
uv run ~/.qoderwork/skills/timeslice-fusion/scripts/timeslice.py run \
  --video panorama.mp4 \
  --selfie me.jpg \
  --output result.mp4 \
  --outputs video gif cover

# 多镜头短片 — 3个镜头 + crossfade 转场
uv run ~/.qoderwork/skills/timeslice-fusion/scripts/timeslice.py run \
  --video panorama.mp4 \
  --selfie me.jpg \
  --output ./output_dir/ \
  --shots 3 \
  --transition crossfade \
  --outputs video cover

# 多镜头 + fade-to-black 转场
uv run ~/.qoderwork/skills/timeslice-fusion/scripts/timeslice.py run \
  --video panorama.mp4 \
  --selfie me.jpg \
  --output ./output_dir/ \
  --shots 3 \
  --transition fade_to_black

# 生成竖屏视频 (适合手机/短视频平台)
uv run ~/.qoderwork/skills/timeslice-fusion/scripts/timeslice.py run \
  --video panorama.mp4 \
  --selfie me.jpg \
  --output result.mp4 \
  --outputs vertical
```

## Parameters

| 参数 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `--video` | 是 | - | 360°风景视频路径 |
| `--selfie` | 是 | - | 自拍照片路径 |
| `--output` | 否 | `./timeslice_output.mp4` | 输出路径 (文件或目录，多产出时建议用目录) |
| `--engine` | 否 | `i2v` | 引擎: `i2v` (真实场景) / `r2v` (AI重绘) |
| `--composite-style` | 否 | `natural` | 合成风格 (仅I2V): `natural` (自然融合) / `collage` (艺术拼贴) |
| `--style` | 否 | `cinematic` | 风格预设 (见下方表格) |
| `--model` | 否 | 自动 | 模型: 自动根据引擎选择 wan2.6-i2v / wan2.6-r2v |
| `--duration` | 否 | `5` | 输出视频时长(秒) |
| `--size` | 否 | `1280*720` | 输出分辨率 (R2V only)，竖版自动切为 720*1280 |
| `--top-n` | 否 | `6` | 粗筛保留帧数 |
| `--api-key` | 否 | 环境变量 | DashScope API Key |
| `--bailian-dir` | 否 | 自动检测 | bailian-multimodal-skills 目录 |
| `--work-dir` | 否 | 临时目录 | 工作目录(存放中间文件) |
| `--vl-model` | 否 | `qwen-vl-max` | 视觉分析模型 |
| `--outputs` | 否 | `video` | 产出类型: video gif vertical cover captioned all |
| `--caption` | 否 | - | 字幕文本 (captioned 产出需要) |
| `--gif-fps` | 否 | `12` | GIF 帧率 |
| `--gif-width` | 否 | `480` | GIF 宽度(像素) |
| `--font` | 否 | 自动检测 | 字幕字体路径 (支持 CJK) |
| `--shots` | 否 | `1` | 镜头数: 1=单镜头, 3=推荐多镜头 |
| `--transition` | 否 | `crossfade` | 转场: `crossfade` (交叉淡入) / `fade_to_black` (黑场) |

## Style Presets

| 风格 | 镜头 | 光影 | 氛围 | 适合场景 |
|------|------|------|------|----------|
| `cinematic` | 缓慢推轨, 浅景深 | 自然光+柔和阴影 | 史诗感 | 壮丽山水, 城市天际线 |
| `dreamy` | 轻微漂浮, 柔焦 | 朦胧散射光 | 梦幻 | 花田, 雾景, 湖面 |
| `epic` | 大广角, 低角度仰拍 | 戏剧性光影对比 | 壮阔 | 雪山, 悬崖, 瀑布 |
| `warm` | 中景, 自然手持感 | 暖色温+逆光 | 温馨 | 小镇, 海滩, 秋林 |
| `noir` | 高对比, 荷兰角 | 单光源强阴影 | 黑色电影 | 城市夜景, 雨天, 建筑 |
| `vintage` | 暗角, 暖色胶片感 | 钨丝灯, 过曝高光 | 怀旧 | 老街, 田野, 咖啡馆 |
| `anime` | 全景, 赛璐珞渲染 | 明亮天空, 光芒效果 | 动漫 | 校园, 花海, 海边 |

## Composite Styles (I2V only)

| 合成风格 | 说明 |
|----------|------|
| `natural` | 自然融合: 45%帧高, 光照适配(亮度/对比/色温), 高斯模糊边缘羽化, 底部20%渐隐, 投射阴影 |
| `collage` | 艺术拼贴: 50%帧高, 2px描边, 2.5°旋转, 明确的"贴上去"视觉风格 |

## Output Types

| 产出类型 | 后缀 | 说明 |
|----------|------|------|
| `video` | `.mp4` | 横版视频 720P (默认) |
| `gif` | `.gif` | 高质量 GIF (palette 方法, 可调帧率/宽度) |
| `vertical` | `_vertical.mp4` | 竖屏视频 9:16 (I2V: 重裁背景+重合成; R2V: 直接生成) |
| `cover` | `_cover.jpg` | 封面图 (视频中间帧) |
| `captioned` | `_captioned.mp4` | 带字幕视频 (需 --caption 参数) |
| `all` | 全部 | 同时生成以上所有产出 |

## Sub-commands

```bash
# 仅提取帧
uv run timeslice.py extract-frames --video input.mp4 --work-dir ./frames/

# 仅分析(帧+自拍)
uv run timeslice.py analyze --work-dir ./frames/ --selfie selfie.jpg

# 仅生成(基于已有分析结果)
uv run timeslice.py generate --work-dir ./frames/ --selfie selfie.jpg --output output.mp4
```

## Progress Log

所有日志以 `[TimeSlice]` 前缀输出到 stderr:

```
[TimeSlice] Starting TimeSlice Fusion v3 pipeline...
[TimeSlice]   Engine:  i2v
[TimeSlice]   Composite: natural
[TimeSlice] Step 0: Using full selfie for I2V (no head-shoulder crop)...
[TimeSlice] Step 0b: Removing selfie background (macOS Vision)...
[TimeSlice] Step 1: Extracting candidate frames from video...
[TimeSlice] Step 2: AI selecting best cinematic angle...
[TimeSlice] Step 3: Deep analyzing scene...
[TimeSlice] Step 4: Analyzing person features...
[TimeSlice] Step 5: Building I2V motion prompt (cinematic style)...
[TimeSlice] Step 5b: Compositing person onto real frame (natural mode)...
[TimeSlice] Step 6: Generating I2V outputs...
[TimeSlice] Done! Total time: 107s
```

## Agent Integration

当用户通过 IM 发送视频和图片时，Agent 应:

1. 下载视频和图片到本地
2. 调用本 skill 的 `run` 命令 (推荐 `--outputs video gif cover`)
3. 默认使用 I2V 引擎 (风景还原)；若用户要求 AI 重绘则加 `--engine r2v`
4. 等待完成(约 1-3 分钟)
5. 将生成的视频/GIF/封面图发送回用户

## Dependencies

- Python >= 3.10 (通过 uv 自动管理)
- ffmpeg (系统级, 用于帧提取和格式转换)
- bailian-multimodal-skills (同仓库, 用于 I2V/R2V 调用)
- Pillow + numpy (自动安装, 用于图像合成和处理)
- macOS (I2V 引擎需要 macOS Vision 框架, 用于人像抠图)
- Swift runtime (macOS 自带, 运行 remove_bg.swift)
