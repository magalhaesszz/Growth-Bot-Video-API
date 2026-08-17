import logging
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import API_SECRET

logger = logging.getLogger("video-api.download")
router = APIRouter()

SUPPORTED_DOMAINS = re.compile(
    r"(instagram\.com|instagr\.am|tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)"
)

TMP_DIR = Path(tempfile.gettempdir()) / "video_downloads"
TMP_DIR.mkdir(exist_ok=True)


def _auth(secret: str):
    if secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


class DownloadRequest(BaseModel):
    url: str


@router.post("/download")
async def download_video(body: DownloadRequest, x_api_secret: str = Header(...)):
    _auth(x_api_secret)

    url = body.url.strip()
    if not SUPPORTED_DOMAINS.search(url):
        raise HTTPException(
            status_code=400,
            detail="URL não suportada. Envie links do Instagram ou TikTok."
        )

    job_id  = uuid.uuid4().hex
    out_dir = TMP_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(out_dir / "%(title).50s.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "mp4/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output", out_tmpl,
        "--no-warnings",
        "--quiet",
        url,
    ]

    try:
        logger.info(f"[download] Baixando: {url}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"[download] yt-dlp stderr: {result.stderr}")
            raise HTTPException(
                status_code=422,
                detail=f"Falha no download: {result.stderr.strip()[:300]}"
            )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Timeout ao baixar o vídeo.")

    # Encontrar o arquivo baixado
    mp4_files = list(out_dir.glob("*.mp4"))
    if not mp4_files:
        raise HTTPException(status_code=500, detail="Arquivo não encontrado após download.")

    mp4_path = mp4_files[0]
    size_mb   = round(mp4_path.stat().st_size / (1024 * 1024), 2)

    logger.info(f"[download] OK — {mp4_path.name} ({size_mb} MB)")

    return JSONResponse({
        "ok":       True,
        "job_id":   job_id,
        "filename": mp4_path.name,
        "size_mb":  size_mb,
        "path":     str(mp4_path),
    })


@router.get("/download/{job_id}")
async def get_downloaded_video(job_id: str, x_api_secret: str = Header(...)):
    """Retorna o vídeo baixado como bytes."""
    _auth(x_api_secret)
    from fastapi.responses import FileResponse

    job_dir = TMP_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    mp4_files = list(job_dir.glob("*.mp4"))
    if not mp4_files:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado.")

    return FileResponse(
        path=str(mp4_files[0]),
        media_type="video/mp4",
        filename=mp4_files[0].name,
    )
