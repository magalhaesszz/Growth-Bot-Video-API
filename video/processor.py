import hashlib
import json
import logging
import os
import random
import subprocess
import time
import re
from collections import Counter
from pathlib import Path

from core.config import DEFAULT_CONFIG, TIMEOUT_FFMPEG

logger = logging.getLogger(__name__)

_CROP_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


# ─── FFprobe ─────────────────────────────────────────────────

def probe_video(path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, timeout=30
    )
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration", 0))
    video_stream = None
    has_audio = False
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not video_stream:
            video_stream = s
            if not duration and "duration" in s:
                duration = float(s["duration"])
        elif s.get("codec_type") == "audio":
            has_audio = True
    if not duration:
        raise ValueError("Não foi possível detectar a duração do vídeo.")
    return {
        "duration": duration,
        "has_audio": has_audio,
        "width":  int(video_stream.get("width",  0)) if video_stream else 0,
        "height": int(video_stream.get("height", 0)) if video_stream else 0,
    }


def _select_stable_crop(output: str, src_w: int, src_h: int) -> tuple[int, int, int, int] | None:
    candidates = [tuple(map(int, match)) for match in _CROP_RE.findall(output)]
    valid = []
    for width, height, x, y in candidates:
        if width <= 0 or height <= 0 or x < 0 or y < 0:
            continue
        if x + width > src_w + 2 or y + height > src_h + 2:
            continue
        retained = width * height / max(1, src_w * src_h)
        removed = 1.0 - retained
        if retained >= 0.35 and removed >= 0.015:
            valid.append((width, height, x, y))
    if not valid:
        return None
    crop, count = Counter(valid).most_common(1)[0]
    # Exige repetição em vários frames para não confundir uma cena escura/clara com borda.
    return crop if count >= 2 else None


def detect_auto_crop(path: str, info: dict, cfg: dict) -> tuple[int, int, int, int] | None:
    if not cfg.get("auto_crop_borders", True):
        return None
    limit = max(0, min(255, int(cfg.get("auto_crop_limit", 24))))
    duration = min(max(info.get("duration", 0), 1), 20)
    detected = []
    for prefix in ("", "negate,"):
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-threads", "1",
            "-i", path, "-t", str(duration),
            "-vf", f"fps=2,{prefix}cropdetect={limit}:2:0",
            "-an", "-f", "null", "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        except subprocess.TimeoutExpired:
            continue
        crop = _select_stable_crop(proc.stderr or "", info["width"], info["height"])
        if crop:
            detected.append(crop)
    if not detected:
        return None
    # Prefere o maior conteúdo estável; evita que uma das passagens corte demais.
    return max(detected, key=lambda item: item[0] * item[1])


# ─── Anti-ban ────────────────────────────────────────────────

def antiban_params(seed: int, cfg: dict) -> dict:
    rng = random.Random(seed)
    sr = cfg.get("speed_range",      DEFAULT_CONFIG["speed_range"])
    br = cfg.get("brightness_range", DEFAULT_CONFIG["brightness_range"])
    sat= cfg.get("saturation_range", DEFAULT_CONFIG["saturation_range"])
    zr = cfg.get("zoom_range",       DEFAULT_CONFIG["zoom_range"])
    fp = cfg.get("flip_chance",      DEFAULT_CONFIG["flip_chance"])
    return {
        "speed":      round(rng.uniform(*sr),  4),
        "brightness": round(rng.uniform(*br),  4),
        "saturation": round(rng.uniform(*sat), 4),
        "zoom":       round(rng.uniform(*zr),  4),
        "flip":       rng.random() < fp,
    }


# ─── Filter complex ──────────────────────────────────────────

def build_filter(cfg: dict, params: dict, src_w: int, src_h: int) -> str:
    cw  = cfg.get("canvas_width",  DEFAULT_CONFIG["canvas_width"])
    ch  = cfg.get("canvas_height", DEFAULT_CONFIG["canvas_height"])
    vw  = cfg.get("video_width",   DEFAULT_CONFIG["video_width"])
    px  = cfg.get("position_x",    DEFAULT_CONFIG["position_x"])
    py  = cfg.get("position_y",    DEFAULT_CONFIG["position_y"])

    speed = params["speed"]
    brightness = params["brightness"]
    saturation = 1.0 + params["saturation"]
    zoom = params["zoom"]

    zoomed_w = int(vw * zoom)
    if zoomed_w % 2 != 0:
        zoomed_w += 1

    vf = []

    # Fix mirror
    if cfg.get("fix_mirror", False):
        vf.append("hflip")

    # Flip anti-ban
    if params.get("flip", False):
        vf.append("hflip")

    # Watermark masks
    for m in cfg.get("watermark_masks", []):
        mw, mh = int(m["w"]), int(m["h"])
        xc = m.get("x", 0)
        yc = m.get("y", 0)
        x = max(0, (src_w - mw) // 2) if xc == "center" else int(xc)
        y = int(src_h * yc) if isinstance(yc, float) and 0 <= yc <= 1 else int(yc)
        if m.get("mode", "blur") == "box":
            vf.append(f"drawbox=x={x}:y={y}:w={mw}:h={mh}:color=black:t=fill")
        else:
            vf.append(f"delogo=x={x}:y={y}:w={mw}:h={mh}")

    detected_crop = cfg.get("_detected_crop")
    if detected_crop:
        vf.append("crop=" + ":".join(str(int(value)) for value in detected_crop))

    vf += [
        f"setpts=PTS/{speed}",
        f"scale={zoomed_w}:-2",
        f"crop={vw}:ih",
        f"eq=brightness={brightness}:saturation={saturation:.4f}",
    ]

    anchors = {"left": 0.0, "center": 0.5, "right": 1.0}
    px = max(0.0, min(1.0, float(anchors.get(px, px))))
    py = max(0.0, min(1.0, float(py)))
    ov_x = f"(main_w-overlay_w)*{px:.4f}"
    ov_y = f"(main_h-overlay_h)*{py:.4f}"

    return (
        f"[0:v]scale={cw}:{ch}[bg];"
        f"[1:v]{','.join(vf)}[vid];"
        f"[bg][vid]overlay={ov_x}:{ov_y}:shortest=1[out]"
    )


# ─── Renderização ────────────────────────────────────────────

def render(
    input_path: str,
    fundo_path: str,
    output_path: str,
    cfg: dict,
    params: dict,
    info: dict,
) -> tuple[bool, str]:
    trim  = cfg.get("trim_start", DEFAULT_CONFIG["trim_start"])
    fps   = cfg.get("output_fps", DEFAULT_CONFIG["output_fps"])
    crf   = cfg.get("output_crf", DEFAULT_CONFIG["output_crf"])
    preset= cfg.get("output_preset", DEFAULT_CONFIG["output_preset"])
    ab    = cfg.get("audio_bitrate", DEFAULT_CONFIG["audio_bitrate"])

    fc = build_filter(cfg, params, info["width"], info["height"])
    af = f"atempo={params['speed']}" if info["has_audio"] else None

    cmd = [
        "ffmpeg", "-y",
        "-loglevel", "error",
        "-filter_threads", "1",
        "-filter_complex_threads", "1",
        "-loop", "1",
        "-i", fundo_path,
        "-ss", str(trim),
        "-i", input_path,
        "-filter_complex", fc,
    ]
    if af:
        cmd += ["-filter:a", af]
    cmd += ["-map", "[out]"]
    if info["has_audio"]:
        cmd += ["-map", "1:a"]
    cmd += [
        "-c:v", "libx264",
        "-threads", "1",
        "-crf", str(crf),
        "-preset", preset,
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
    ]
    if info["has_audio"]:
        cmd += ["-c:a", "aac", "-b:a", ab]
    cmd += [
        "-shortest",
        "-map_metadata", "-1",
        "-fflags", "+bitexact",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_FFMPEG
        )
    except subprocess.TimeoutExpired:
        return False, "Timeout: vídeo demorou mais que o limite permitido."

    if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return True, "ok"

    lines = [l for l in (proc.stderr or "").splitlines() if l.strip()]
    if proc.returncode in {-9, 137}:
        return False, "O Railway encerrou o FFmpeg por falta de memoria. Tente um video menor ou aumente a memoria do servico."
    return False, lines[-1] if lines else f"FFmpeg código {proc.returncode}"


def render_preview(input_path: str, fundo_path: str, output_path: str, cfg: dict = None) -> dict:
    """Renderiza o primeiro frame com o mesmo layout usado no vídeo final."""
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    try:
        info = probe_video(input_path)
    except Exception as exc:
        return {"ok": False, "error": f"FFprobe falhou: {exc}"}

    cfg["_detected_crop"] = detect_auto_crop(input_path, info, cfg)
    params = {"speed": 1.0, "brightness": 0.0, "saturation": 0.0, "zoom": 1.0, "flip": False}
    fc = build_filter(cfg, params, info["width"], info["height"])
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", fundo_path,
        "-ss", str(cfg.get("trim_start", DEFAULT_CONFIG["trim_start"])), "-i", input_path,
        "-filter_complex", fc, "-map", "[out]",
        "-frames:v", "1", "-q:v", "2", output_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timeout ao gerar preview."}
    if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return {"ok": True}
    lines = [line for line in (proc.stderr or "").splitlines() if line.strip()]
    return {"ok": False, "error": lines[-1] if lines else "Falha ao gerar preview."}


# ─── Função principal ────────────────────────────────────────

def process_video(
    input_path: str,
    fundo_path: str,
    output_path: str,
    cfg: dict = None,
    filename: str = "video.mp4",
) -> dict:
    """
    Processa um vídeo e salva em output_path.
    Retorna dict com status, tempo, tamanho e params usados.
    """
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    t0 = time.time()

    try:
        info = probe_video(input_path)
    except Exception as e:
        return {"ok": False, "error": f"FFprobe falhou: {e}"}

    cfg["_detected_crop"] = detect_auto_crop(input_path, info, cfg)

    if info["duration"] - cfg["trim_start"] < 2.0:
        return {"ok": False, "error": "Vídeo muito curto (mínimo 2 segundos úteis)."}

    seed = int(hashlib.md5(filename.encode()).hexdigest()[:8], 16)

    if cfg.get("antiban", True):
        params = antiban_params(seed, cfg)
    else:
        params = {"speed": 1.0, "brightness": 0.0, "saturation": 0.0, "zoom": 1.0, "flip": False}

    ok, msg = render(input_path, fundo_path, output_path, cfg, params, info)
    elapsed = round(time.time() - t0, 1)

    if ok:
        size_mb = round(os.path.getsize(output_path) / (1024 * 1024), 2)
        return {
            "ok": True,
            "elapsed_s": elapsed,
            "size_mb": size_mb,
            "params": params,
            "info": info,
        }
    return {"ok": False, "error": msg, "elapsed_s": elapsed}
