import json
import os
import shutil
import time
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from core.config import (
    API_SECRET, FUNDO_DIR, INPUT_DIR, OUTPUT_DIR,
    MAX_FUNDO_MB, MAX_VIDEO_MB,
)
from video.processor import process_video, render_preview
from video.validator import validate_fundo, validate_video

router = APIRouter()

# Fundo padrão persistido por conta (key = account_id, value = path)
_fundos: dict[str, str] = {}


def _auth(secret: Optional[str]):
    if secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Não autorizado.")


def _save_upload(upload: UploadFile, dest: str):
    with open(dest, "wb") as f:
        shutil.copyfileobj(upload.file, f)


# ─── POST /fundo ─────────────────────────────────────────────

@router.post("/fundo")
async def salvar_fundo(
    fundo: UploadFile = File(...),
    account_id: str  = Form("default"),
    x_api_secret: Optional[str] = Header(None),
):
    """Salva a imagem de fundo para uma conta. Deve ser PNG 1080x1920."""
    _auth(x_api_secret)

    if not fundo.filename.lower().endswith((".png", ".jpg", ".jpeg")):
        raise HTTPException(400, "Envie uma imagem PNG ou JPG.")

    size_mb = fundo.size / (1024 * 1024) if fundo.size else 0
    if size_mb > MAX_FUNDO_MB:
        raise HTTPException(400, f"Imagem muito grande ({size_mb:.1f} MB, máximo {MAX_FUNDO_MB} MB).")

    ext  = os.path.splitext(fundo.filename)[1]
    path = os.path.join(FUNDO_DIR, f"{account_id}{ext}")
    _save_upload(fundo, path)

    ok, msg = validate_fundo(path)
    if not ok:
        os.remove(path)
        raise HTTPException(400, msg)

    _fundos[account_id] = path
    return {"ok": True, "message": f"Fundo salvo para conta {account_id}."}


# ─── GET /fundo ──────────────────────────────────────────────

@router.get("/fundo")
async def ver_fundo(
    account_id: str = "default",
    x_api_secret: Optional[str] = Header(None),
):
    _auth(x_api_secret)
    path = _fundos.get(account_id)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Nenhum fundo cadastrado para essa conta.")
    return FileResponse(path, media_type="image/png", filename="fundo.png")


# ─── POST /processar ─────────────────────────────────────────

@router.post("/processar")
async def processar_video(
    video:      UploadFile = File(...),
    account_id: str  = Form("default"),
    config_json:str  = Form("{}"),
    x_api_secret: Optional[str] = Header(None),
):
    """
    Recebe um .mp4, processa com FFmpeg e retorna o vídeo editado.
    Configurações opcionais via config_json (JSON string).
    """
    _auth(x_api_secret)

    fundo_path = _fundos.get(account_id)
    if not fundo_path or not os.path.exists(fundo_path):
        raise HTTPException(400, f"Nenhum fundo cadastrado para conta '{account_id}'. Use POST /fundo primeiro.")

    if not video.filename.lower().endswith(".mp4"):
        raise HTTPException(400, "Envie um arquivo .mp4.")

    try:
        cfg = json.loads(config_json)
    except Exception:
        raise HTTPException(400, "config_json inválido. Envie um JSON válido.")

    job_id    = str(uuid.uuid4())[:8]
    input_path  = os.path.join(INPUT_DIR,  f"{job_id}_in.mp4")
    output_path = os.path.join(OUTPUT_DIR, f"{job_id}_out.mp4")

    _save_upload(video, input_path)

    ok_v, msg_v = validate_video(input_path, MAX_VIDEO_MB)
    if not ok_v:
        os.remove(input_path)
        raise HTTPException(400, msg_v)

    result = process_video(
        input_path=input_path,
        fundo_path=fundo_path,
        output_path=output_path,
        cfg=cfg,
        filename=video.filename,
    )

    os.remove(input_path)

    if not result["ok"]:
        raise HTTPException(500, f"Falha no processamento: {result.get('error')}")

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"editado_{video.filename}",
        background=_cleanup_after(output_path),
    )


@router.post("/preview")
async def preview_video(
    video: UploadFile = File(...),
    account_id: str = Form("default"),
    config_json: str = Form("{}"),
    x_api_secret: Optional[str] = Header(None),
):
    """Retorna uma imagem JPG do layout antes do processamento final."""
    _auth(x_api_secret)
    fundo_path = _fundos.get(account_id)
    if not fundo_path or not os.path.exists(fundo_path):
        raise HTTPException(400, f"Nenhum fundo cadastrado para conta '{account_id}'.")
    if not video.filename.lower().endswith(".mp4"):
        raise HTTPException(400, "Envie um arquivo .mp4.")
    try:
        cfg = json.loads(config_json)
    except Exception:
        raise HTTPException(400, "config_json inválido.")

    job_id = str(uuid.uuid4())[:8]
    input_path = os.path.join(INPUT_DIR, f"{job_id}_preview_in.mp4")
    output_path = os.path.join(OUTPUT_DIR, f"{job_id}_preview.jpg")
    _save_upload(video, input_path)
    try:
        ok_v, msg_v = validate_video(input_path, MAX_VIDEO_MB)
        if not ok_v:
            raise HTTPException(400, msg_v)
        result = render_preview(input_path, fundo_path, output_path, cfg)
        if not result["ok"]:
            raise HTTPException(500, result.get("error", "Falha ao gerar preview."))
        return FileResponse(
            output_path, media_type="image/jpeg", filename="preview.jpg",
            background=_cleanup_after(output_path),
        )
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)


# ─── POST /processar/lote ────────────────────────────────────

@router.post("/processar/lote")
async def processar_lote(
    videos:     list[UploadFile] = File(...),
    account_id: str  = Form("default"),
    config_json:str  = Form("{}"),
    x_api_secret: Optional[str] = Header(None),
):
    """
    Processa múltiplos .mp4 e retorna JSON com links temporários para download.
    Limite: 10 vídeos por chamada.
    """
    _auth(x_api_secret)

    if len(videos) > 10:
        raise HTTPException(400, "Máximo de 10 vídeos por lote.")

    fundo_path = _fundos.get(account_id)
    if not fundo_path or not os.path.exists(fundo_path):
        raise HTTPException(400, f"Nenhum fundo cadastrado para conta '{account_id}'.")

    try:
        cfg = json.loads(config_json)
    except Exception:
        raise HTTPException(400, "config_json inválido.")

    resultados = []
    for video in videos:
        job_id      = str(uuid.uuid4())[:8]
        input_path  = os.path.join(INPUT_DIR,  f"{job_id}_in.mp4")
        output_path = os.path.join(OUTPUT_DIR, f"{job_id}_out.mp4")

        _save_upload(video, input_path)

        ok_v, msg_v = validate_video(input_path, MAX_VIDEO_MB)
        if not ok_v:
            os.remove(input_path)
            resultados.append({"arquivo": video.filename, "ok": False, "error": msg_v})
            continue

        result = process_video(
            input_path=input_path,
            fundo_path=fundo_path,
            output_path=output_path,
            cfg=cfg,
            filename=video.filename,
        )
        os.remove(input_path)

        if result["ok"]:
            resultados.append({
                "arquivo":    video.filename,
                "ok":         True,
                "job_id":     job_id,
                "download":   f"/download/{job_id}",
                "elapsed_s":  result["elapsed_s"],
                "size_mb":    result["size_mb"],
                "params":     result["params"],
            })
        else:
            resultados.append({"arquivo": video.filename, "ok": False, "error": result.get("error")})

    return {"total": len(videos), "resultados": resultados}


# ─── GET /download/{job_id} ──────────────────────────────────

@router.get("/download/{job_id}")
async def download(job_id: str, x_api_secret: Optional[str] = Header(None)):
    """Download de um vídeo processado em lote."""
    _auth(x_api_secret)
    path = os.path.join(OUTPUT_DIR, f"{job_id}_out.mp4")
    if not os.path.exists(path):
        raise HTTPException(404, "Arquivo não encontrado ou já expirou.")
    return FileResponse(
        path, media_type="video/mp4",
        filename=f"editado_{job_id}.mp4",
        background=_cleanup_after(path),
    )


# ─── GET /status ─────────────────────────────────────────────

@router.get("/status")
async def status(x_api_secret: Optional[str] = Header(None)):
    """Retorna status da API e informações do ambiente."""
    _auth(x_api_secret)
    import shutil as sh
    import subprocess
    ffmpeg_ok = False
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        ffmpeg_ok = True
    except Exception:
        pass

    disk = sh.disk_usage("/tmp")
    return {
        "ok": True,
        "ffmpeg": ffmpeg_ok,
        "fundos_cadastrados": len(_fundos),
        "contas": list(_fundos.keys()),
        "disco_tmp_livre_mb": round(disk.free / (1024 * 1024), 1),
        "videos_em_fila": len(os.listdir(INPUT_DIR)),
        "videos_prontos": len(os.listdir(OUTPUT_DIR)),
    }


# ─── GET /config/default ─────────────────────────────────────

@router.get("/config/default")
async def config_default(x_api_secret: Optional[str] = Header(None)):
    """Retorna as configurações padrão do processador."""
    _auth(x_api_secret)
    from core.config import DEFAULT_CONFIG
    return DEFAULT_CONFIG


# ─── DELETE /limpar ──────────────────────────────────────────

@router.delete("/limpar")
async def limpar_tmp(x_api_secret: Optional[str] = Header(None)):
    """Limpa arquivos temporários de output."""
    _auth(x_api_secret)
    removed = 0
    for f in os.listdir(OUTPUT_DIR):
        try:
            os.remove(os.path.join(OUTPUT_DIR, f))
            removed += 1
        except Exception:
            pass
    return {"ok": True, "removidos": removed}


# ─── Helpers ─────────────────────────────────────────────────

class _cleanup_after:
    """Background task: remove o arquivo após o envio."""
    def __init__(self, path: str):
        self.path = path

    async def __call__(self):
        await __import__("asyncio").sleep(5)
        try:
            os.remove(self.path)
        except Exception:
            pass
