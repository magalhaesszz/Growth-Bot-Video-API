import logging
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config import API_SECRET

logger = logging.getLogger("video-api.download")
router = APIRouter()

SUPPORTED = re.compile(
    r"(instagram\.com|instagr\.am|tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)"
)

TMP = Path(tempfile.gettempdir()) / "video_downloads"
TMP.mkdir(exist_ok=True)

# Arquivo de cookies do TikTok (opcional — melhora compatibilidade)
COOKIES_FILE = Path("/tmp/tiktok_cookies.txt")


def _auth(secret: str):
    if secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


class DownloadRequest(BaseModel):
    url: str


def _build_cmd(url: str, out_tmpl: str) -> list[str]:
    """Monta o comando yt-dlp com as melhores opções para cada plataforma."""
    is_tiktok = "tiktok.com" in url

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "mp4/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output", out_tmpl,
        "--no-warnings",
        "--quiet",
        "--no-check-certificates",
        "--add-header", "User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    ]

    if is_tiktok:
        # Opções específicas para TikTok
        cmd += [
            "--extractor-args", "tiktok:api_hostname=api22-normal-c-useast2a.tiktokv.com",
        ]
        # Usar cookies se disponível
        if COOKIES_FILE.exists():
            cmd += ["--cookies", str(COOKIES_FILE)]

    cmd.append(url)
    return cmd


@router.post("/download")
async def download_video(body: DownloadRequest, x_api_secret: str = Header(...)):
    """Baixa vídeo do Instagram ou TikTok e retorna os bytes."""
    _auth(x_api_secret)

    url = body.url.strip()
    if not SUPPORTED.search(url):
        raise HTTPException(400, "URL não suportada. Use links do Instagram ou TikTok.")

    job_dir = TMP / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(job_dir / "%(title).50s.%(ext)s")

    cmd = _build_cmd(url, out_tmpl)
    logger.info(f"[download] {url}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise HTTPException(408, "Timeout ao baixar o vídeo.")

    # Se falhou com TikTok, tentar com abordagem alternativa
    if result.returncode != 0 and "tiktok" in url.lower():
        logger.warning(f"[download] Tentativa 1 falhou, tentando fallback TikTok...")
        cmd_alt = [
            "yt-dlp",
            "--no-playlist",
            "--format", "best",
            "--output", out_tmpl,
            "--no-warnings",
            "--quiet",
            "--no-check-certificates",
            "--add-header", "User-Agent:com.zhiliaoapp.musically/2022600030 (Linux; U; Android 12; en_US; Pixel 4; Build/SQ3A.220705.004; Cronet/58.0.2991.0)",
            url,
        ]
        result = subprocess.run(cmd_alt, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        err = result.stderr.strip()[:400]
        logger.error(f"[download] falhou: {err}")

        # Mensagem de erro amigável
        if "403" in err or "status code 0" in err or "status code 10240" in err:
            raise HTTPException(422,
                "TikTok bloqueou o download pelo IP do servidor. "
                "Envie o vídeo direto como arquivo .mp4 ou use o Instagram.")
        raise HTTPException(422, f"Falha no download: {err}")

    mp4_files = list(job_dir.glob("*.mp4"))
    if not mp4_files:
        # Tentar qualquer arquivo de vídeo
        all_files = list(job_dir.glob("*"))
        if all_files:
            mp4_files = [all_files[0]]
        else:
            raise HTTPException(500, "Arquivo não encontrado após download.")

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


@router.post("/cookies/tiktok")
async def set_tiktok_cookies(
    cookies_content: str,
    x_api_secret: str = Header(...),
):
    """Salva cookies do TikTok para melhorar compatibilidade de download."""
    _auth(x_api_secret)
    try:
        COOKIES_FILE.write_text(cookies_content)
        return {"ok": True, "message": "Cookies salvos com sucesso."}
    except Exception as e:
        raise HTTPException(500, f"Erro ao salvar cookies: {e}")
