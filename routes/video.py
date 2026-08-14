import json
import os
import shutil
import time
import uuid
import subprocess
import re
from typing import Optional

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool

from core.config import (
    API_SECRET, FUNDO_DIR, INPUT_DIR, OUTPUT_DIR,
    MAX_FUNDO_MB, MAX_VIDEO_MB, DEFAULT_CONFIG,
)
from video.processor import detect_auto_crop, probe_video, process_video, render_preview
from video.validator import validate_fundo, validate_video
from editor_ui import editor_html

router = APIRouter()

# Fundo padrão persistido por conta (key = account_id, value = path)
_fundos: dict[str, str] = {}
_editor_sessions: dict[str, dict] = {}


def _safe_account_id(account_id: str) -> str:
    value = str(account_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise HTTPException(400, "Identificador de conta invalido.")
    return value


def _find_fundo(account_id: str) -> Optional[str]:
    """Recupera o fundo do disco mesmo depois de um restart da API."""
    account_id = _safe_account_id(account_id)
    cached = _fundos.get(account_id)
    if cached and os.path.isfile(cached):
        return cached
    for ext in (".png", ".jpg", ".jpeg"):
        candidate = os.path.join(FUNDO_DIR, f"{account_id}{ext}")
        if os.path.isfile(candidate):
            _fundos[account_id] = candidate
            return candidate
    return None


def _load_fundos_from_disk() -> None:
    for filename in os.listdir(FUNDO_DIR):
        stem, ext = os.path.splitext(filename)
        if ext.lower() in {".png", ".jpg", ".jpeg"} and re.fullmatch(
            r"[A-Za-z0-9_-]{1,80}", stem
        ):
            _fundos[stem] = os.path.join(FUNDO_DIR, filename)


def _cleanup_editor_sessions(max_age_s: int = 3600):
    now = time.time()
    expired = [token for token, session in _editor_sessions.items() if now - session["created_at"] > max_age_s]
    for token in expired:
        session = _editor_sessions.pop(token)
        frame_path = session.get("frame_path")
        if frame_path and os.path.exists(frame_path):
            os.remove(frame_path)


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
    account_id = _safe_account_id(account_id)

    if not fundo.filename.lower().endswith((".png", ".jpg", ".jpeg")):
        raise HTTPException(400, "Envie uma imagem PNG ou JPG.")

    size_mb = fundo.size / (1024 * 1024) if fundo.size else 0
    if size_mb > MAX_FUNDO_MB:
        raise HTTPException(400, f"Imagem muito grande ({size_mb:.1f} MB, máximo {MAX_FUNDO_MB} MB).")

    ext = os.path.splitext(fundo.filename)[1].lower()
    for old_ext in (".png", ".jpg", ".jpeg"):
        old_path = os.path.join(FUNDO_DIR, f"{account_id}{old_ext}")
        if old_ext != ext.lower() and os.path.isfile(old_path):
            os.remove(old_path)
    path = os.path.join(FUNDO_DIR, f"{account_id}{ext}")
    await run_in_threadpool(_save_upload, fundo, path)

    ok, msg = await run_in_threadpool(validate_fundo, path)
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
    path = _find_fundo(account_id)
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

    fundo_path = _find_fundo(account_id)
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

    await run_in_threadpool(_save_upload, video, input_path)

    ok_v, msg_v = await run_in_threadpool(validate_video, input_path, MAX_VIDEO_MB)
    if not ok_v:
        os.remove(input_path)
        raise HTTPException(400, msg_v)

    result = await run_in_threadpool(
        process_video, input_path, fundo_path, output_path, cfg, video.filename
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
    fundo_path = _find_fundo(account_id)
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
    await run_in_threadpool(_save_upload, video, input_path)
    try:
        ok_v, msg_v = await run_in_threadpool(validate_video, input_path, MAX_VIDEO_MB)
        if not ok_v:
            raise HTTPException(400, msg_v)
        result = await run_in_threadpool(render_preview, input_path, fundo_path, output_path, cfg)
        if not result["ok"]:
            raise HTTPException(500, result.get("error", "Falha ao gerar preview."))
        return FileResponse(
            output_path, media_type="image/jpeg", filename="preview.jpg",
            background=_cleanup_after(output_path),
        )
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)


@router.post("/editor/session")
async def criar_editor_session(
    request: Request,
    video: UploadFile = File(...),
    account_id: str = Form("default"),
    config_json: str = Form("{}"),
    x_api_secret: Optional[str] = Header(None),
):
    _auth(x_api_secret)
    _cleanup_editor_sessions()
    fundo_path = _find_fundo(account_id)
    if not fundo_path or not os.path.exists(fundo_path):
        raise HTTPException(400, "Envie o fundo antes de abrir o editor.")
    try:
        cfg = json.loads(config_json)
    except Exception:
        raise HTTPException(400, "config_json inválido.")
    token = uuid.uuid4().hex
    input_path = os.path.join(INPUT_DIR, f"{token}_editor.mp4")
    frame_path = os.path.join(OUTPUT_DIR, f"{token}_frame.jpg")
    await run_in_threadpool(_save_upload, video, input_path)
    try:
        ok_v, msg_v = await run_in_threadpool(validate_video, input_path, MAX_VIDEO_MB)
        if not ok_v:
            raise HTTPException(400, msg_v)
        info = await run_in_threadpool(probe_video, input_path)
        detected_crop = await run_in_threadpool(
            detect_auto_crop, input_path, info, {**DEFAULT_CONFIG, **cfg}
        )
        frame_cmd = ["ffmpeg", "-y", "-loglevel", "error", "-threads", "1", "-ss", "0.1", "-i", input_path]
        if detected_crop:
            frame_cmd += ["-vf", "crop=" + ":".join(str(value) for value in detected_crop)]
        frame_cmd += ["-frames:v", "1", "-q:v", "2", frame_path]
        proc = await run_in_threadpool(
            subprocess.run, frame_cmd, capture_output=True, text=True, timeout=60
        )
        if proc.returncode != 0 or not os.path.exists(frame_path):
            raise HTTPException(500, "Não foi possível extrair o frame para o editor.")
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)
    cfg = {**DEFAULT_CONFIG, **cfg}
    _editor_sessions[token] = {
        "account_id": account_id, "fundo_path": fundo_path, "frame_path": frame_path,
        "config": cfg, "created_at": time.time(),
    }
    editor_url = str(request.base_url).rstrip("/") + f"/api/v1/editor/{token}"
    return {"ok": True, "token": token, "editor_url": editor_url}


def _editor(token: str) -> dict:
    _cleanup_editor_sessions()
    session = _editor_sessions.get(token)
    if not session:
        raise HTTPException(404, "Sessão do editor expirada.")
    return session


@router.get("/editor/{token}", response_class=HTMLResponse)
async def abrir_editor(token: str):
    _editor(token)
    return editor_html(token)


@router.get("/editor/{token}/background")
async def editor_background(token: str):
    return FileResponse(_editor(token)["fundo_path"])


@router.get("/editor/{token}/frame")
async def editor_frame(token: str):
    return FileResponse(_editor(token)["frame_path"], media_type="image/jpeg")


@router.get("/editor/{token}/config")
async def editor_config(token: str):
    return _editor(token)["config"]


@router.put("/editor/{token}/config")
async def salvar_editor_config(token: str, config: dict = Body(...)):
    try:
        values = {
            "video_width": max(100, min(1080, int(config["video_width"]))),
            "position_x": max(0.0, min(1.0, float(config["position_x"]))),
            "position_y": max(0.0, min(1.0, float(config["position_y"]))),
        }
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Configuração inválida.")
    _editor(token)["config"].update(values)
    return {"ok": True}


@router.get("/editor/{token}/result")
async def editor_result(token: str, x_api_secret: Optional[str] = Header(None)):
    _auth(x_api_secret)
    return {"ok": True, "config": _editor(token)["config"]}


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

    fundo_path = _find_fundo(account_id)
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

        await run_in_threadpool(_save_upload, video, input_path)

        ok_v, msg_v = await run_in_threadpool(validate_video, input_path, MAX_VIDEO_MB)
        if not ok_v:
            os.remove(input_path)
            resultados.append({"arquivo": video.filename, "ok": False, "error": msg_v})
            continue

        result = await run_in_threadpool(
            process_video, input_path, fundo_path, output_path, cfg, video.filename
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
    if not re.fullmatch(r"[a-f0-9]{8}", job_id):
        raise HTTPException(400, "Identificador de arquivo invalido.")
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
    _load_fundos_from_disk()
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
