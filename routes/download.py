import logging
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from core.config import API_SECRET

logger = logging.getLogger("video-api.download")
router = APIRouter()

SUPPORTED = re.compile(
    r"(instagram\.com|instagr\.am|tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)"
)

TMP = Path(tempfile.gettempdir()) / "video_downloads"
TMP.mkdir(exist_ok=True)


def _auth(secret: str):
    if secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


class DownloadRequest(BaseModel):
    url: str


@router.post("/download")
async def download_video(body: DownloadRequest, x_api_secret: str = Header(...)):
    """Baixa vídeo e retorna os bytes diretamente (sem job_id)."""
    _auth(x_api_secret)

    url = body.url.strip()
    if not SUPPORTED.search(url):
        raise HTTPException(400, "URL nao suportada. Use links do Instagram ou TikTok.")

    job_dir = TMP / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(job_dir / "%(title).50s.%(ext)s")

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

    logger.info(f"[download] {url}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise HTTPException(408, "Timeout ao baixar o video.")

    if result.returncode != 0:
        err = result.stderr.strip()[:300]
        logger.error(f"[download] yt-dlp falhou: {err}")
        raise HTTPException(422, f"Falha no download: {err}")

    mp4_files = list(job_dir.glob("*.mp4"))
    if not mp4_files:
        raise HTTPException(500, "Arquivo nao encontrado apos download.")

    mp4 = mp4_files[0]
    size_mb = round(mp4.stat().st_size / (1024 * 1024), 2)
    logger.info(f"[download] OK — {mp4.name} ({size_mb} MB)")

    # Retorna o arquivo de video diretamente
    return FileResponse(
        path=str(mp4),
        media_type="video/mp4",
        filename=mp4.name,
        headers={
            "X-Filename": mp4.name,
            "X-Size-MB":  str(size_mb),
        },
    )
