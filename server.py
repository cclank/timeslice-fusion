# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fastapi>=0.115.0",
#     "uvicorn>=0.32.0",
#     "python-multipart>=0.0.18",
#     "openai>=1.0.0",
#     "dashscope>=1.25.8",
#     "requests>=2.31.0",
#     "pillow>=10.0.0",
#     "numpy>=1.24.0",
# ]
# ///
"""
TimeSlice Fusion — API Server
FastAPI + SSE, subprocess pipeline execution with real-time progress.

Usage:
    uv run server.py                          # default port 8000
    PORT=3000 uv run server.py                # custom port
    CLEANUP_AFTER_MINUTES=0 uv run server.py  # disable auto-cleanup
"""

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ── Constants ──────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
SCRIPTS_DIR = BASE_DIR / "scripts"
WEB_DIR = BASE_DIR / "web"
TASKS_DIR = BASE_DIR / "tasks"

STEP_PATTERNS = [
    ("Removing selfie background", 0, "人像抠图"),
    ("Using full selfie", 0, "人像抠图"),
    ("Extracting candidate frames", 1, "提取候选帧"),
    ("AI selecting", 2, "AI 选帧"),
    ("Deep analyzing scene", 3, "场景 + 人物分析"),
    ("Analyzing person", 3, "场景 + 人物分析"),
    ("Compositing", 4, "人景合成"),
    ("Building I2V motion", 4, "人景合成"),
    ("Generating I2V", 5, "I2V 视频生成"),
    ("Generating R2V", 5, "视频生成"),
]

STEP_NAMES = ["人像抠图", "提取候选帧", "AI 选帧", "场景 + 人物分析", "人景合成", "I2V 视频生成"]
STEP_PERCENTS = [0, 10, 25, 45, 65, 80]

INTERMEDIATE_WHITELIST = {
    "selfie_cutout.png", "contact_sheet.jpg",
    "composite_natural_shot00.jpg", "composite_collage_shot00.jpg",
    "i2v_prompt.txt", "analysis.json",
}

CLEANUP_MINUTES = int(os.environ.get("CLEANUP_AFTER_MINUTES", "60"))

# ── In-memory task store ───────────────────────────────

tasks: dict[str, dict[str, Any]] = {}

# ── App ────────────────────────────────────────────────

app = FastAPI(title="TimeSlice Fusion API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Helpers ────────────────────────────────────────────

def parse_step(line: str) -> tuple[int, str] | None:
    """Match a [TimeSlice] log line to a pipeline step index."""
    for pattern, idx, name in STEP_PATTERNS:
        if pattern in line:
            return idx, name
    return None


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def find_intermediates(work_dir: Path) -> dict[str, str]:
    """Scan work_dir for known intermediate files."""
    result = {}
    if not work_dir.exists():
        return result
    for f in work_dir.iterdir():
        if f.name in INTERMEDIATE_WHITELIST:
            result[f.stem] = f.name
        elif f.name.startswith("frame_") and f.suffix in (".jpg", ".png"):
            result[f.name] = f.name
        elif f.name.startswith("composite_") and f.suffix in (".jpg", ".png"):
            result["composite"] = f.name
        elif f.name.startswith("shot_") and f.suffix == ".mp4":
            result[f.stem] = f.name
    # Also check for best_frame
    for f in work_dir.glob("best_frame*"):
        result["best_frame"] = f.name
    return result


async def run_pipeline_subprocess(task_id: str):
    """Launch timeslice.py as subprocess, parse output, update task state."""
    task = tasks[task_id]
    task_dir = Path(task["task_dir"])
    work_dir = task_dir / "work"
    work_dir.mkdir(exist_ok=True)
    output_path = task_dir / "output.mp4"

    # Use uv run to ensure timeslice.py's PEP 723 inline deps are resolved
    cmd = [
        "uv", "run", str(SCRIPTS_DIR / "timeslice.py"), "run",
        "--video", task["video_path"],
        "--selfie", task["selfie_path"],
        "--output", str(output_path),
        "--work-dir", str(work_dir),
        "--style", task["style"],
        "--composite-style", task["composite_style"],
        "--duration", str(task["duration"]),
        "--outputs", "video", "cover",
    ]

    task["status"] = "running"
    task["current_step"] = -1
    task["logs"] = []

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        task["pid"] = proc.pid

        # Read stderr line by line for progress
        while True:
            line_bytes = await proc.stderr.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            task["logs"].append(line)

            step_info = parse_step(line)
            if step_info:
                step_idx, step_name = step_info
                task["current_step"] = step_idx
                task["current_step_name"] = step_name

        # Read stdout for MEDIA lines
        stdout_bytes = await proc.stdout.read()
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        media_paths = []
        for l in stdout_text.strip().split("\n"):
            if l.startswith("MEDIA:"):
                media_paths.append(l.split("MEDIA:", 1)[1].strip())

        await proc.wait()

        if proc.returncode == 0:
            # Find the output video
            video_path = None
            # output_path may be a file or directory (multi-output mode creates a dir)
            if output_path.is_file() and str(output_path).endswith(".mp4"):
                video_path = str(output_path)
            elif output_path.is_dir():
                # Look for .mp4 inside the output directory
                mp4s = list(output_path.glob("*.mp4"))
                if mp4s:
                    video_path = str(mp4s[0])
            # Fallback to MEDIA: lines from stdout
            if not video_path and media_paths:
                for mp in media_paths:
                    if mp.endswith(".mp4") and os.path.exists(mp):
                        video_path = mp
                        break

            task["status"] = "done"
            task["output_path"] = video_path
            task["intermediates"] = find_intermediates(work_dir)
            task["current_step"] = len(STEP_NAMES)
        else:
            last_logs = task["logs"][-5:] if task["logs"] else ["Unknown error"]
            task["status"] = "error"
            task["error"] = "\n".join(last_logs)

    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)


# ── Routes ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse("/web/timeslice-fusion.html")


@app.post("/api/generate")
async def generate(
    video: UploadFile = File(...),
    selfie: UploadFile = File(...),
    style: str = Form("cinematic"),
    composite_style: str = Form("natural"),
    duration: int = Form(5),
):
    task_id = uuid.uuid4().hex[:12]
    task_dir = TASKS_DIR / f"ts_task_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)

    # Save uploaded files
    video_path = task_dir / f"input_video{Path(video.filename).suffix}"
    selfie_path = task_dir / f"input_selfie{Path(selfie.filename).suffix}"

    with open(video_path, "wb") as f:
        content = await video.read()
        f.write(content)

    with open(selfie_path, "wb") as f:
        content = await selfie.read()
        f.write(content)

    # Register task
    tasks[task_id] = {
        "task_id": task_id,
        "task_dir": str(task_dir),
        "video_path": str(video_path),
        "selfie_path": str(selfie_path),
        "style": style,
        "composite_style": composite_style,
        "duration": duration,
        "status": "queued",
        "current_step": -1,
        "current_step_name": "",
        "logs": [],
        "output_path": None,
        "intermediates": {},
        "error": None,
        "created_at": time.time(),
    }

    # Launch pipeline in background
    asyncio.create_task(run_pipeline_subprocess(task_id))

    return {"task_id": task_id}


@app.get("/api/progress/{task_id}")
async def progress(task_id: str):
    if task_id not in tasks:
        return StreamingResponse(
            iter([sse_event("error", {"message": "任务不存在"})]),
            media_type="text/event-stream",
        )

    async def event_stream():
        last_step = -1
        last_log_count = 0
        keepalive_interval = 15

        # Send initial snapshot
        task = tasks[task_id]
        if task["current_step"] >= 0:
            for i in range(task["current_step"] + 1):
                status = "done" if i < task["current_step"] else "running"
                yield sse_event("step", {"step": i, "name": STEP_NAMES[i], "status": status})
            pct = STEP_PERCENTS[min(task["current_step"], len(STEP_PERCENTS) - 1)]
            yield sse_event("progress", {"percent": pct, "message": f"{task.get('current_step_name', '')}..."})

        while True:
            task = tasks.get(task_id)
            if not task:
                yield sse_event("error", {"message": "任务已被清理"})
                break

            # Check for new step
            current_step = task["current_step"]
            if current_step > last_step:
                # Mark previous steps as done
                for i in range(max(0, last_step), current_step):
                    yield sse_event("step", {"step": i, "name": STEP_NAMES[i], "status": "done"})
                # Mark current step as running
                if 0 <= current_step < len(STEP_NAMES):
                    yield sse_event("step", {"step": current_step, "name": STEP_NAMES[current_step], "status": "running"})
                    pct = STEP_PERCENTS[current_step]
                    yield sse_event("progress", {"percent": pct, "message": f"{STEP_NAMES[current_step]}..."})
                last_step = current_step

            # Forward new log lines
            log_count = len(task["logs"])
            if log_count > last_log_count:
                for line in task["logs"][last_log_count:log_count]:
                    yield sse_event("log", {"message": line})
                last_log_count = log_count

            # Check terminal states
            if task["status"] == "done":
                # Mark all steps done
                for i in range(len(STEP_NAMES)):
                    yield sse_event("step", {"step": i, "name": STEP_NAMES[i], "status": "done"})
                yield sse_event("progress", {"percent": 100, "message": "生成完成"})

                result = {"video_url": f"/api/result/{task_id}"}
                if task.get("intermediates"):
                    intermediates = {}
                    for key, fname in task["intermediates"].items():
                        intermediates[key] = f"/api/result/{task_id}/intermediate/{fname}"
                    result["intermediates"] = intermediates
                yield sse_event("done", result)
                break

            elif task["status"] == "error":
                yield sse_event("error", {"message": task.get("error", "未知错误")})
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/result/{task_id}")
async def result_video(task_id: str):
    task = tasks.get(task_id)
    if not task or not task.get("output_path"):
        return {"error": "结果不存在"}
    path = Path(task["output_path"])
    if not path.exists():
        return {"error": "视频文件不存在"}
    return FileResponse(path, media_type="video/mp4", filename=f"timeslice_{task_id}.mp4")


@app.get("/api/result/{task_id}/intermediate/{filename}")
async def result_intermediate(task_id: str, filename: str):
    task = tasks.get(task_id)
    if not task:
        return {"error": "任务不存在"}

    # Security: validate filename
    if "/" in filename or "\\" in filename or ".." in filename:
        return {"error": "非法文件名"}

    work_dir = Path(task["task_dir"]) / "work"
    file_path = work_dir / filename
    if not file_path.exists() or not file_path.is_file():
        return {"error": "文件不存在"}

    suffix = file_path.suffix.lower()
    media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".mp4": "video/mp4", ".txt": "text/plain", ".json": "application/json"}
    return FileResponse(file_path, media_type=media_types.get(suffix, "application/octet-stream"))


# ── Static files ───────────────────────────────────────

app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")


# ── Cleanup background task ───────────────────────────

async def cleanup_old_tasks():
    """Periodically remove expired task directories."""
    if CLEANUP_MINUTES <= 0:
        return
    while True:
        await asyncio.sleep(300)  # check every 5 minutes
        now = time.time()
        expired = [tid for tid, t in tasks.items()
                   if t["status"] in ("done", "error")
                   and now - t["created_at"] > CLEANUP_MINUTES * 60]
        for tid in expired:
            task = tasks.pop(tid, None)
            if task:
                task_dir = Path(task["task_dir"])
                if task_dir.exists():
                    shutil.rmtree(task_dir, ignore_errors=True)


@app.on_event("startup")
async def startup():
    TASKS_DIR.mkdir(exist_ok=True)
    asyncio.create_task(cleanup_old_tasks())


# ── Entry point ────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    print(f"\n  TimeSlice Fusion API")
    print(f"  http://localhost:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
