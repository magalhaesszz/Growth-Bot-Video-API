import logging
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config import API_SECRET, PROXY_URL

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


def _build_cmd(url: str, out_tmpl: str, attempt: int = 1) -> list[str]:
    is_tiktok = "tiktok.com" in url

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--merge-output-format", "mp4",
        "--output", out_tmpl,
        "--no-warnings",
        "--quiet",
        "--no-check-certificates",
    ]

    # Usar proxy residencial se configurado
    if PROXY_URL:
        cmd += ["--proxy", PROXY_URL]
        logger.info(f"[download] Usando proxy: {PROXY_URL.split('@')[-1]}")

    if attempt == 1:
        cmd += [
            "--format", "mp4/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--add-header", "User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        ]
        if is_tiktok:
            cmd += ["--extractor-args", "tiktok:api_hostname=api22-normal-c-useast2a.tiktokv.com"]
    else:
        # Fallback: user-agent do app TikTok Android
        cmd += [
            "--format", "best",
            "--add-header", "User-Agent:com.zhiliaoapp.musically/2022600030 (Linux; U; Android 12; en_US; Pixel 4; Build/SQ3A.220705.004)",
        ]

    cmd.append(url)
    return cmd


@router.post("/download")
async def download_video(body: DownloadRequest, x_api_secret: str = Header(...)):
    _auth(x_api_secret)

    url = body.url.strip()
    if not SUPPORTED.search(url):
        raise HTTPException(400, "URL nao suportada. Use links do Instagram ou TikTok.")

    job_dir = TMP / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(job_dir / "%(title).50s.%(ext)s")

    # Tentativa 1
    cmd = _build_cmd(url, out_tmpl, attempt=1)
    logger.info(f"[download] tentativa 1: {url}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise HTTPException(408, "Timeout ao baixar o video.")

    # Tentativa 2 se falhou
    if result.returncode != 0:
        logger.warning(f"[download] tentativa 1 falhou, tentando fallback...")
        cmd2 = _build_cmd(url, out_tmpl, attempt=2)
        try:
            result = subprocess.run(cmd2, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            raise HTTPException(408, "Timeout ao baixar o video.")

    if result.returncode != 0:
        err = result.stderr.strip()[:400]
        logger.error(f"[download] falhou definitivamente: {err}")
        is_tiktok = "tiktok.com" in url
        if is_tiktok and not PROXY_URL:
            raise HTTPException(422,
                "TikTok bloqueou o download. Configure PROXY_URL no Railway "
                "ou envie o video direto como arquivo .mp4.")
        raise HTTPException(422, f"Falha no download: {err}")

    mp4_files = list(job_dir.glob("*.mp4"))
    if not mp4_files:
        all_files = list(job_dir.glob("*"))
        if all_files:
            mp4_files = [all_files[0]]
        else:
            raise HTTPException(500, "Arquivo nao encontrado apos download.")

    mp4 = mp4_files[0]
    size_mb = round(mp4.stat().st_size / (1024 * 1024), 2)
    logger.info(f"[download] OK — {mp4.name} ({size_mb} MB)")

    return FileResponse(
        path=str(mp4),
        media_type="video/mp4",
        filename=mp4.name,
        headers={
            "X-Filename": mp4.name,
            "X-Size-MB":  str(size_mb),
        },
    )
