import logging
import subprocess
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.video import router as video_router
from routes.editor_batch import router as editor_batch_router

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("video-api")


def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, timeout=10)
        logger.info("FFmpeg disponível.")
    except Exception:
        logger.critical("FFmpeg não encontrado! A API não funcionará sem ele.")
        sys.exit(1)


check_ffmpeg()

app = FastAPI(
    title="Growth Bot — Video API",
    description="API de processamento de vídeo 9:16 com anti-ban para Instagram/TikTok.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(video_router, prefix="/api/v1", tags=["vídeo"])
app.include_router(editor_batch_router, prefix="/api/v1", tags=["editor em massa"])

from routes.download import router as download_router
from routes.edit import router as edit_router
app.include_router(download_router, prefix="/api/v1", tags=["download"])
app.include_router(edit_router, prefix="/api/v1", tags=["edição"])


@app.get("/", tags=["health"])
async def root():
    return {"status": "online", "service": "Growth Bot Video API", "version": "1.0.0"}


@app.get("/health", tags=["health"])
async def health():
    return {"ok": True}
