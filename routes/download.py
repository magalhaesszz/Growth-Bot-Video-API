import logging
import re
import subprocess
import tempfile
import uuid
import urllib.request
import urllib.error
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse, Response
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


def _download_tiktok_via_api(url: str) -> bytes | None:
    """
    Tenta baixar vídeo do TikTok via API pública do tikwm.com
    que contorna o bloqueio de IP de datacenter.
    """
    try:
        import urllib.parse
        body = urllib.parse.urlencode({"url": url, "hd": "1"}).encode()
        req = urllib.request.Request(
            "https://www.tikwm.com/api/",
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.tikwm.com/",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
            if data.get("code") == 0 and data.get("data"):
                play_url = data["data"].get("hdplay") or data["data"].get("play")
                if play_url:
                    logger.info(f"[tiktok-api] URL obtida: {play_url[:60]}")
                    # Baixar o vídeo
                    video_req = urllib.request.Request(
                        play_url,
                        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tiktok.com/"}
                    )
                    with urllib.request.urlopen(video_req, timeout=60) as vr:
                        return vr.read()
    except Exception as e:
        logger.warning(f"[tiktok-api] tikwm falhou: {e}")

    # Tentar via ssstik
    try:
        import urllib.parse
        body2 = urllib.parse.urlencode({
            "id": url,
            "locale": "pt",
            "tt": "d2ViX3R0",
        }).encode()
        req2 = urllib.request.Request(
            "https://ssstik.io/abc?url=dl",
            data=body2,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://ssstik.io/",
                "HX-Request": "true",
            }
        )
        with urllib.request.urlopen(req2, timeout=15) as r:
            html = r.read().decode()
            # Extrair URL do mp4
            match = re.search(r'href="(https://[^"]+\.mp4[^"]*)"', html)
            if match:
                mp4_url = match.group(1)
                logger.info(f"[ssstik] URL: {mp4_url[:60]}")
                vreq = urllib.request.Request(
                    mp4_url,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://ssstik.io/"}
                )
                with urllib.request.urlopen(vreq, timeout=60) as vr:
                    return vr.read()
    except Exception as e:
        logger.warning(f"[tiktok-api] ssstik falhou: {e}")

    return None


def _download_via_ytdlp(url: str, out_tmpl: str) -> bool:
    """Tenta yt-dlp com proxy se disponível."""
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "mp4/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output", out_tmpl,
        "--no-warnings",
        "--quiet",
        "--no-check-certificates",
    ]
    if PROXY_URL:
        cmd += ["--proxy", PROXY_URL]

    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.returncode == 0


import json


@router.post("/download")
async def download_video(body: DownloadRequest, x_api_secret: str = Header(...)):
    _auth(x_api_secret)

    url = body.url.strip()
    if not SUPPORTED.search(url):
        raise HTTPException(400, "URL nao suportada. Use links do Instagram ou TikTok.")

    is_tiktok = "tiktok.com" in url
    job_dir = TMP / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)

    # Para TikTok: tentar via API externa primeiro (contorna bloqueio de IP)
    if is_tiktok:
        logger.info(f"[download] TikTok detectado — tentando API externa...")
        video_bytes = _download_tiktok_via_api(url)
        if video_bytes and len(video_bytes) > 10000:
            filename = f"tiktok_{uuid.uuid4().hex[:8]}.mp4"
            out_path = job_dir / filename
            out_path.write_bytes(video_bytes)
            size_mb = round(len(video_bytes) / (1024 * 1024), 2)
            logger.info(f"[download] TikTok via API OK — {size_mb} MB")
            return FileResponse(
                path=str(out_path),
                media_type="video/mp4",
                filename=filename,
                headers={"X-Filename": filename, "X-Size-MB": str(size_mb)},
            )
        logger.warning("[download] API externa falhou — tentando yt-dlp...")

    # yt-dlp para Instagram ou fallback TikTok
    out_tmpl = str(job_dir / "%(title).50s.%(ext)s")
    try:
        ok = _download_via_ytdlp(url, out_tmpl)
    except subprocess.TimeoutExpired:
        raise HTTPException(408, "Timeout ao baixar o video.")

    if not ok and is_tiktok:
        raise HTTPException(422,
            "Nao foi possivel baixar este video do TikTok. "
            "Tente salvar o video no celular e enviar direto como arquivo .mp4.")

    if not ok:
        raise HTTPException(422, "Falha no download. Verifique o link e tente novamente.")

    mp4_files = list(job_dir.glob("*.mp4"))
    if not mp4_files:
        all_files = list(job_dir.glob("*"))
        if not all_files:
            raise HTTPException(500, "Arquivo nao encontrado apos download.")
        mp4_files = [all_files[0]]

    mp4 = mp4_files[0]
    size_mb = round(mp4.stat().st_size / (1024 * 1024), 2)
    filename = mp4.name
    logger.info(f"[download] OK — {filename} ({size_mb} MB)")

    return FileResponse(
        path=str(mp4),
        media_type="video/mp4",
        filename=filename,
        headers={"X-Filename": filename, "X-Size-MB": str(size_mb)},
    )
