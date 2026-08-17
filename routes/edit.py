import logging
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from core.config import API_SECRET

logger = logging.getLogger("video-api.edit")
router = APIRouter()

TMP_DIR = Path(tempfile.gettempdir()) / "video_edits"
TMP_DIR.mkdir(exist_ok=True)


def _auth(secret: str):
    if secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _safe_text(text: str) -> str:
    """Escapa texto para uso seguro no ffmpeg drawtext."""
    return text.replace("\\", "\\\\").replace("'", "").replace(":", " ").replace("%", "%%")


@router.post("/editar")
async def editar_video(
    video: UploadFile = File(...),
    watermark_text: str  = Form(""),
    caption_text: str    = Form(""),
    crop_start: float    = Form(0.0),
    crop_end: float      = Form(0.0),
    x_api_secret: str    = Header(...),
):
    _auth(x_api_secret)

    job_id  = uuid.uuid4().hex
    job_dir = TMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    input_path  = job_dir / "input.mp4"
    output_path = job_dir / "output.mp4"
    input_path.write_bytes(await video.read())

    vf_filters = []
    time_args  = []

    if crop_start > 0:
        time_args += ["-ss", str(crop_start)]
    if crop_end > 0 and crop_end > crop_start:
        time_args += ["-t", str(crop_end - crop_start)]

    if watermark_text:
        wm = _safe_text(watermark_text)
        vf_filters.append(
            f"drawtext=text='{wm}':fontsize=36:fontcolor=white:alpha=0.8"
            ":x=w-tw-20:y=20:box=1:boxcolor=black@0.4:boxborderw=6"
        )

    if caption_text:
        cap = _safe_text(caption_text)
        vf_filters.append(
            f"drawtext=text='{cap}':fontsize=40:fontcolor=white:alpha=0.95"
            ":x=(w-tw)/2:y=h-th-40:box=1:boxcolor=black@0.5:boxborderw=8"
        )

    cmd = ["ffmpeg", "-y"] + time_args + ["-i", str(input_path)]
    if vf_filters:
        cmd += ["-vf", ",".join(vf_filters)]
    cmd += ["-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(output_path)]

    logger.info(f"[editar] job={job_id}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        logger.error(f"[editar] ffmpeg: {result.stderr[-300:]}")
        raise HTTPException(status_code=500, detail=f"FFmpeg falhou: {result.stderr[-200:]}")

    size_mb = round(output_path.stat().st_size / (1024 * 1024), 2)
    logger.info(f"[editar] OK — {size_mb} MB")

    return FileResponse(
        path=str(output_path),
        media_type="video/mp4",
        filename=f"editado_{video.filename}",
        headers={"X-Size-MB": str(size_mb)},
    )
