import os
from dotenv import load_dotenv

load_dotenv()

# ─── Segurança ───────────────────────────────────────────────
API_SECRET = os.getenv("API_SECRET", "change-me-in-production")

# ─── Diretórios de trabalho ──────────────────────────────────
TMP_DIR   = os.path.join("/tmp", "video-api")
INPUT_DIR = os.path.join(TMP_DIR, "input")
OUTPUT_DIR= os.path.join(TMP_DIR, "output")
FUNDO_DIR = os.path.join(TMP_DIR, "fundos")

for d in [INPUT_DIR, OUTPUT_DIR, FUNDO_DIR]:
    os.makedirs(d, exist_ok=True)

# ─── Limites ─────────────────────────────────────────────────
MAX_VIDEO_MB      = 200          # tamanho máximo do vídeo de entrada
MAX_FUNDO_MB      = 10
TIMEOUT_FFMPEG    = 300          # segundos máximos por vídeo

# ─── Defaults do processador ─────────────────────────────────
DEFAULT_CONFIG = {
    "canvas_width":   1080,
    "canvas_height":  1920,
    "video_width":    800,
    "position_x":     "center",
    "position_y":     0.25,
    "output_fps":     30,
    "output_crf":     18,
    "output_preset":  "fast",       # Railway tem CPU limitada — "fast" é melhor que "slow"
    "audio_bitrate":  "192k",
    "trim_start":     0.1,
    "speed_range":    [1.02, 1.05],
    "brightness_range": [0.01, 0.02],
    "saturation_range": [-0.02, 0.02],
    "zoom_range":     [1.01, 1.03],
    "fix_mirror":     False,
    "flip_chance":    0.5,
    "watermark_masks": [],
    "antiban":        True,
}
