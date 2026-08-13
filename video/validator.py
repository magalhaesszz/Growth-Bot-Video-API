import json
import subprocess
from core.config import DEFAULT_CONFIG


def validate_fundo(path: str) -> tuple[bool, str]:
    """Valida se o fundo é PNG 1080x1920."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True, text=True, timeout=15
        )
        data = json.loads(result.stdout)
        streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
        if not streams:
            return False, "Imagem inválida ou formato não suportado."
        w = streams[0].get("width",  0)
        h = streams[0].get("height", 0)
        cw = DEFAULT_CONFIG["canvas_width"]
        ch = DEFAULT_CONFIG["canvas_height"]
        if w != cw or h != ch:
            return False, f"Fundo deve ser {cw}x{ch}px. Enviado: {w}x{h}px."
        return True, "ok"
    except Exception as e:
        return False, f"Erro ao validar fundo: {e}"


def validate_video(path: str, max_mb: float) -> tuple[bool, str]:
    """Valida tamanho e se é vídeo válido."""
    import os
    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > max_mb:
        return False, f"Vídeo muito grande: {size_mb:.1f} MB (máximo {max_mb} MB)."
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True, text=True, timeout=15
        )
        data = json.loads(result.stdout)
        has_video = any(s.get("codec_type") == "video" for s in data.get("streams", []))
        if not has_video:
            return False, "Arquivo não contém stream de vídeo."
        return True, "ok"
    except Exception as e:
        return False, f"Erro ao validar vídeo: {e}"
