#!/usr/bin/env python3
"""
PS Vita Boot Animation Creator v2
==================================
Crea animaciones de arranque personalizadas para PS Vita con Henkaku Enso.

Modos:
    python app.py                  # Servidor web (http://127.0.0.1:8080)
    python app.py --desktop        # Interfaz grafica nativa (tkinter)
    python app.py --port 9090      # Puerto personalizado

Requiere: pip install Pillow
Opcional: FFmpeg en PATH para videos (https://ffmpeg.org/download.html)
"""

import http.server
import io
import json
import os
import shutil
import struct
import gzip
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import webbrowser
import uuid
import mimetypes
import time
import ftplib
import socket
from pathlib import Path

try:
    from PIL import Image, ImageSequence
except ImportError:
    Image = None

# ─── Constantes ───────────────────────────────────────────────────────────
RCF_MAGIC = 0x56464352
ANIM_VERSION = 1
MAX_FILE_SIZE = 300 * 1024 * 1024
MAX_ANIM_SIZE = 108 * 1024 * 1024

RESOLUCIONES = {
    "960x544 (Full Vita)": (960, 544),
    "768x408": (768, 408),
    "640x368": (640, 368),
    "512x272 (Mapped 480x272)": (512, 272),
    "480x272 (PSP-like)": (480, 272),
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_UPLOAD_DIR = os.path.join(BASE_DIR, "tmp_upload")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
# Directorio Vita SD para copia automatica (ajustar segun montura local)
VITA_SD_DIRS = [
    "D:/PSVitaBootAnim",  # Lector de tarjetas SD
    "E:/PSVitaBootAnim",
    "F:/PSVitaBootAnim",
    "G:/PSVitaBootAnim",
    "H:/PSVitaBootAnim",
    "I:/PSVitaBootAnim",
    os.path.expanduser("~/Desktop/VitaBootAnim"),
]


# ─── Deteccion de dependencias ───────────────────────────────────────────

def check_ffmpeg():
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False

def check_ffprobe():
    try:
        r = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False

def check_deps():
    deps = {"pillow": Image is not None, "ffmpeg": check_ffmpeg(), "ffprobe": check_ffprobe()}
    if deps["ffmpeg"]:
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
            deps["ffmpeg_version"] = r.stdout.split("\n")[0] if r.stdout else "ffmpeg"
        except Exception:
            pass
    return deps


# ─── Error personalizado ──────────────────────────────────────────────────

class ConversionError(Exception):
    pass


# ─── Extraccion de frames (soporta GIF sin FFmpeg) ────────────────────────

def extract_frames_from_gif(gif_path, res_w, res_h, log_cb):
    log_cb("Extrayendo frames desde GIF (Pillow)...")
    img = Image.open(gif_path)
    frames = []
    tmp_dir = tempfile.mkdtemp(prefix="vita_gif_")
    try:
        for i, frame in enumerate(ImageSequence.Iterator(img)):
            out = os.path.join(tmp_dir, f"frame_{i:05d}.png")
            f = frame.convert("RGBA").resize((res_w, res_h), Image.LANCZOS)
            f.save(out)
            frames.append(out)
            if (i + 1) % 20 == 0 or i == 0:
                log_cb(f"  Frame {i+1} ({out})")
        log_cb(f"Frames extraidos: {len(frames)}")
        return frames, tmp_dir
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def extract_frames_from_video(video_path, res_w, res_h, fps, log_cb):
    log_cb("Extrayendo frames del video (FFmpeg)...")

    if not check_ffprobe():
        raise ConversionError(
            "FFprobe no encontrado. Instala FFmpeg desde:\n"
            "  https://ffmpeg.org/download.html\n"
            "O usa un archivo GIF directamente (no necesita FFmpeg)."
        )

    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,width,height,duration,nb_frames",
        "-of", "json", video_path
    ], capture_output=True, text=True, timeout=30)

    info = json.loads(probe.stdout) if probe.stdout else {}
    stream = info.get("streams", [{}])[0] if info.get("streams") else {}

    if fps is None or fps <= 0:
        fr = stream.get("r_frame_rate", "30/1")
        if "/" in fr:
            n, d = fr.split("/")
            fps = round(float(n) / float(d)) if float(d) != 0 else 30
        else:
            fps = 30

    duration = float(stream.get("duration", 0))
    if duration <= 0:
        duration = 10

    max_frames = int(duration * fps)
    if max_frames > 900:
        target = 600
        fps = max(1, round(target / duration))
        max_frames = 600
        log_cb(f"Video largo: limitando a ~{max_frames} frames ({fps} fps)")

    log_cb(f"Dimension: {stream.get('width','?')}x{stream.get('height','?')}")
    log_cb(f"Duracion: {duration:.1f}s | FPS: {fps} | Frames: ~{max_frames}")

    tmp_dir = tempfile.mkdtemp(prefix="vita_video_")
    pattern = os.path.join(tmp_dir, "frame_%05d.png")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps={fps},scale={res_w}:{res_h}:flags=lanczos",
        "-frames:v", str(max_frames),
        "-sws_flags", "lanczos", "-an",
        pattern
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise ConversionError(f"FFmpeg error: {proc.stderr[:500]}")

    frames = sorted(Path(tmp_dir).glob("frame_*.png"))
    result = [str(f) for f in frames]
    log_cb(f"Frames extraidos: {len(result)}")

    if not result:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise ConversionError("No se extrajeron frames del video")

    return result, tmp_dir


# ─── Conversion a RCF / CBS ──────────────────────────────────────────────

def convert_frames(frames, output_path, fmt, res_w, res_h, compression, flags, end_logo_data, log_cb):
    log_cb("Comprimiendo y empaquetando...")
    if fmt in ("rcf", "both"):
        _build_rcf(frames, os.path.join(output_path, "boot.rcf"),
                   res_w, res_h, compression, flags, end_logo_data, log_cb)
    if fmt in ("cbs", "both"):
        _build_cbs(frames, os.path.join(output_path, "boot_animation.img"),
                   compression, flags, log_cb)
    log_cb("Conversion completada!")


def _build_rcf(frames, out_path, res_w, res_h, compression, flags, end_logo_data, log_cb):
    log_cb("Creando boot.rcf (vita-bootanim)...")

    header = bytearray()
    header += struct.pack('<I', RCF_MAGIC)       # magic
    header += struct.pack('B', ANIM_VERSION)     # version
    header += struct.pack('B', 1 if flags.get('cache') else 0)
    header += struct.pack('B', flags.get('priority', 6))
    header += struct.pack('B', 1 if flags.get('sweep') else 0)
    header += struct.pack('B', 1 if flags.get('vblank') else 0)
    header += struct.pack('B', 1 if flags.get('swap') else 0)
    header += struct.pack('B', 1 if flags.get('sram') else 0)
    header += struct.pack('B', 1 if end_logo_data else 0)  # fullres_frame
    header += struct.pack('B', 1 if flags.get('loop') else 0)
    header += struct.pack('<i', len(frames))
    header += struct.pack('<H', res_h)
    header += struct.pack('<H', res_w)

    with open(out_path, 'wb') as f:
        f.write(header)

        # End logo frame opcional
        if end_logo_data:
            logo_comp = gzip.compress(end_logo_data, compression)
            f.write(struct.pack('<I', len(logo_comp)))
            f.write(logo_comp)
            log_cb(f"  Logo final: {_fmt_size(len(logo_comp))}")

        total = len(frames)
        for i, frame_path in enumerate(frames):
            img = Image.open(frame_path).convert("RGBA")
            raw = img.tobytes()
            comp = gzip.compress(raw, compression)
            f.write(struct.pack('<I', len(comp)))
            f.write(comp)
            if (i + 1) % 20 == 0 or i == total - 1:
                pct = int((i + 1) / total * 100)
                log_cb(f"  Frame {i+1}/{total} ({pct}%)")

    size = os.path.getsize(out_path)
    log_cb(f"boot.rcf: {_fmt_size(size)}")
    if size > MAX_ANIM_SIZE:
        log_cb("ADVERTENCIA: Supera 108 MB - puede no cargar en la Vita")
    else:
        log_cb("OK: Dentro del limite de 108 MB")


def _build_cbs(frames, out_path, compression, flags, log_cb):
    log_cb("Creando boot_animation.img (CBS-Manager)...")
    cbs_flags = bytearray([
        0 if flags.get('loop', True) else 1,
        0,
        1 if flags.get('nopreload') else 0,
        1 if flags.get('slowmode') else 0,
    ])
    with open(out_path, 'wb') as f:
        f.write(struct.pack('<I', 0))
        f.write(cbs_flags)
        total = len(frames)
        for i, frame_path in enumerate(frames):
            img = Image.open(frame_path).convert("RGBA")
            raw = img.tobytes()
            comp = gzip.compress(raw, compression)
            f.write(struct.pack('<I', len(comp)))
            f.write(comp)
            if (i + 1) % 20 == 0 or i == total - 1:
                pct = int((i + 1) / total * 100)
                log_cb(f"  Frame {i+1}/{total} ({pct}%)")
        f.seek(0)
        f.write(struct.pack('<I', total))
    log_cb(f"boot_animation.img: {_fmt_size(os.path.getsize(out_path))}")


def process_frame_for_logo(image_data, res_w, res_h):
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        img = Image.open(image_data if hasattr(image_data, 'read') else image_data)
        img = img.convert("RGBA")
        img = img.resize((960, 544), Image.LANCZOS)
        img.save(tmp_path, "PNG")
        return img.tobytes()
    finally:
        try: os.unlink(tmp_path)
        except: pass


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ─── Generacion de ejemplos (GIFs con Pillow) ────────────────────────────

def _gen_example_ps1():
    """Genera una animacion estilo PS1 boot (texto PS bouncing)"""
    frames = []
    w, h = 960, 544
    colors = [(0, 0, 139), (0, 0, 180), (0, 0, 200), (0, 0, 220)]
    for i in range(30):
        img = Image.new("RGBA", (w, h), (0, 0, 30, 255))
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        # Texto "PS" que rebota
        offset_y = abs(12 - (i % 24)) * 8
        offset_x = int(10 * (i % 20 - 10))
        try:
            font_large = ImageFont.truetype("arial.ttf", 180)
            font_small = ImageFont.truetype("arial.ttf", 60)
        except Exception:
            font_large = ImageFont.load_default()
            font_small = font_large
        col = colors[i % len(colors)]
        # Draw "PS" large
        draw.text((w//2 - 100 + offset_x//2, h//3 - 30 + offset_y), "PS",
                  fill=col, font=font_large)
        draw.text((w//2 - 100 + offset_x//2, h//2 + 30), "VITA BOOT",
                  fill=(180, 180, 200, 255), font=font_small)
        buf = io.BytesIO()
        img.save(buf, format="GIF", transparency=0)
        buf.seek(0)
        frames.append(Image.open(buf).copy())
        buf.close()

    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=80, disposal=2)
    return out.getvalue()


def _gen_example_gameboy():
    """Genera animacion estilo GameBoy boot (logo verde que aparece)"""
    frames = []
    w, h = 960, 544
    green_bg = (30, 50, 20, 255)
    green_text = (100, 180, 80, 255)
    for i in range(25):
        img = Image.new("RGBA", (w, h), green_bg)
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 140)
            font2 = ImageFont.truetype("arial.ttf", 50)
        except Exception:
            font = ImageFont.load_default()
            font2 = font
        # Texto que se desliza desde arriba
        slide = max(0, 100 - i * 5)
        alpha = min(255, i * 20)
        draw.text((w//2 - 200, 80 + slide), "GAME BOY", fill=(*green_text[:3], alpha), font=font)
        draw.text((w//2 - 120, 270), "BOOT ANIMATION", fill=(80, 140, 60, alpha), font=font2)
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        frames.append(Image.open(buf).copy())
        buf.close()

    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=100, disposal=2)
    return out.getvalue()


def _gen_example_switch():
    """Genera animacion estilo Nintendo Switch (circulos rojo/azul)"""
    frames = []
    w, h = 960, 544
    for i in range(30):
        img = Image.new("RGBA", (w, h), (20, 20, 30, 255))
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        phase = i / 30.0
        r1 = int(200 + 50 * (phase * 6.28))
        r2 = int(200 + 50 * ((phase + 0.5) * 6.28))
        cx1, cy1 = w//2 - 120, h//2
        cx2, cy2 = w//2 + 120, h//2
        draw.ellipse([cx1 - r1//2, cy1 - r1//2, cx1 + r1//2, cy1 + r1//2],
                     fill=(230, 40, 40, 200))
        draw.ellipse([cx2 - r2//2, cy2 - r2//2, cx2 + r2//2, cy2 + r2//2],
                     fill=(40, 120, 230, 200))
        # Linea central
        draw.rectangle([w//2 - 5, h//2 - 80, w//2 + 5, h//2 + 80],
                       fill=(200, 200, 200, 255))
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        frames.append(Image.open(buf).copy())
        buf.close()

    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=80, disposal=2)
    return out.getvalue()


def _gen_example_dreamcast():
    """Estilo SEGA Dreamcast (espiral azul)"""
    frames = []
    w, h = 960, 544
    import math
    for i in range(36):
        img = Image.new("RGBA", (w, h), (20, 40, 80, 255))
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        cx, cy = w//2, h//2
        phase = i / 36.0
        for r in range(0, 200, 10):
            a = (phase * 4 + r * 0.05) * 3.14159 * 2
            x = cx + int(r * math.cos(a))
            y = cy + int(r * math.sin(a))
            draw.ellipse([x-15, y-15, x+15, y+15], fill=(60, 140, 255, 180))
        # anillo mas pequeno
        for r in range(0, 80, 6):
            a = (phase * 2 - r * 0.08) * 3.14159 * 2
            x = cx + int(r * math.cos(a))
            y = cy + int(r * math.sin(a))
            draw.ellipse([x-8, y-8, x+8, y+8], fill=(200, 220, 255, 200))
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        frames.append(Image.open(buf).copy())
        buf.close()
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=60, disposal=2)
    return out.getvalue()


def _gen_example_snes():
    """Estilo SNES/Super Famicom (gris con botones de colores)"""
    frames = []
    w, h = 960, 544
    for i in range(20):
        img = Image.new("RGBA", (w, h), (80, 76, 90, 255))
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 120)
            font_s = ImageFont.truetype("arial.ttf", 40)
        except Exception:
            font = ImageFont.load_default()
            font_s = font
        draw.text((w//2 - 140, 60), "SUPER", fill=(200, 200, 210, 255), font=font)
        draw.text((w//2 - 180, 180), "NINTENDO", fill=(200, 200, 210, 255), font=font_s)
        # Botones de colores
        btn_w, btn_h = 60, 60
        yb = h - 150
        spacing = 120
        cx = w//2 - spacing*1.5
        colors = [(255, 50, 50), (50, 200, 50), (50, 100, 255), (255, 200, 50)]
        labels = ["B", "A", "Y", "X"]
        for col, label in zip(colors, labels):
            draw.ellipse([cx, yb, cx+btn_w, yb+btn_h], fill=(*col, 200))
            draw.text((cx+18, yb+15), label, fill=(255, 255, 255, 255), font=font_s)
            cx += spacing
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        frames.append(Image.open(buf).copy())
        buf.close()
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=120, disposal=2)
    return out.getvalue()


def _gen_example_gamecube():
    """Estilo GameCube (cubo giratorio simplificado)"""
    frames = []
    w, h = 960, 544
    import math
    for i in range(30):
        img = Image.new("RGBA", (w, h), (40, 50, 70, 255))
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        cx, cy = w//2, h//2 - 20
        angle = (i / 30.0) * 3.14159 * 2
        # Cubo 3D simplificado - 3 cuadrados que rotan
        pts = []
        for j in range(4):
            a = angle + j * 3.14159 / 2
            rad = 120
            px = cx + int(rad * math.cos(a))
            py = cy + int(rad * math.sin(a) * 0.5)
            pts.append((px, py))
        draw.polygon(pts, fill=(80, 130, 200, 200), outline=(150, 200, 255, 255))
        # Cuadrado interior
        pts2 = []
        for j in range(4):
            a = angle + 3.14159/4 + j * 3.14159 / 2
            rad = 60
            px = cx + int(rad * math.cos(a))
            py = cy + int(rad * math.sin(a) * 0.5)
            pts2.append((px, py))
        draw.polygon(pts2, fill=(200, 80, 80, 200), outline=(255, 150, 150, 255))
        # Texto
        from PIL import ImageDraw as ID
        try:
            from PIL import ImageFont
            font = ImageFont.truetype("arial.ttf", 50)
        except Exception:
            font = ImageFont.load_default()
        # omit text draw, just keep visual
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        frames.append(Image.open(buf).copy())
        buf.close()
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=60, disposal=2)
    return out.getvalue()


def _gen_example_ps2():
    """Estilo PS2 (cubos azules + texto)"""
    frames = []
    w, h = 960, 544
    import math
    for i in range(30):
        img = Image.new("RGBA", (w, h), (10, 20, 60, 255))
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 160)
        except Exception:
            font = ImageFont.load_default()
        draw.text((w//2 - 150, 100), "PS2", fill=(60, 120, 255, 255), font=font)
        # Cubos flotantes
        for j in range(6):
            a = (i / 30.0) * 3.14159 * 2 + j * 1.0
            ox = int(80 * math.cos(a) + (j-2.5) * 110) + w//2
            oy = int(60 * math.sin(a * 0.7)) + h//2 + 50
            sz = 20 + int(10 * math.sin(a * 2))
            draw.rectangle([ox-sz, oy-sz, ox+sz, oy+sz],
                          fill=(40, 100 + j*20, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        frames.append(Image.open(buf).copy())
        buf.close()
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=60, disposal=2)
    return out.getvalue()


def _gen_example_sega():
    """Estilo SEGA Genesis/Mega Drive (logo azul)"""
    frames = []
    w, h = 960, 544
    for i in range(20):
        img = Image.new("RGBA", (w, h), (20, 20, 80, 255))
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 180)
            font_s = ImageFont.truetype("arial.ttf", 50)
        except Exception:
            font = ImageFont.load_default()
            font_s = font
        # Gradiente vertical simulado
        for y in range(h):
            r = 20 + int((i % 10) * 3)
            g = 20 + int((i % 10) * 5)
            b = 80 + int((i % 10) * 8)
            draw.point((w//2, y), fill=(r, g, b, 255))
        # No gradient, just text
        draw.text((w//2 - 170, 100), "SEGA", fill=(60, 160, 255, 255), font=font)
        draw.text((w//2 - 160, 280), "GENESIS", fill=(60, 160, 255, 200), font=font_s)
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        frames.append(Image.open(buf).copy())
        buf.close()
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=100, disposal=2)
    return out.getvalue()


def _gen_example_wii():
    """Estilo Nintendo Wii (logo verde/azul)"""
    frames = []
    w, h = 960, 544
    import math
    for i in range(25):
        img = Image.new("RGBA", (w, h), (30, 40, 50, 255))
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 140)
        except Exception:
            font = ImageFont.load_default()
        # Letras W i i con colores
        draw.text((w//2 - 180, 80), "W", fill=(70, 200, 255, 255), font=font)
        draw.text((w//2 - 50, 80), "i", fill=(100, 230, 100, 255), font=font)
        draw.text((w//2 + 50, 80), "i", fill=(100, 230, 100, 255), font=font)
        # Circulos de colores alrededor
        for j in range(8):
            a = (i / 25.0) * 3.14159 * 2 + j * 0.785
            cx = w//2 + int(200 * math.cos(a))
            cy = h//2 + 80 + int(150 * math.sin(a))
            col = [(70, 200, 255), (100, 230, 100), (255, 200, 50), (255, 100, 100)][j % 4]
            draw.ellipse([cx-15, cy-15, cx+15, cy+15], fill=(*col, 180))
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        frames.append(Image.open(buf).copy())
        buf.close()
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=80, disposal=2)
    return out.getvalue()


def _gen_example_win95():
    """Estilo Windows 95 (ventana clasica + logo)"""
    frames = []
    w, h = 960, 544
    for i in range(20):
        img = Image.new("RGBA", (w, h), (0, 128, 128, 255))
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 70)
            font_s = ImageFont.truetype("arial.ttf", 35)
        except Exception:
            font = ImageFont.load_default()
            font_s = font
        # Barra de titulo de ventana
        draw.rectangle([50, 50, w-50, 120], fill=(0, 0, 128, 255))
        draw.text((70, 60), "Microsoft Windows 95", fill=(255, 255, 255, 255), font=font_s)
        # Bandera (rectangulos de colores)
        flag_y = 180
        bw, bh = 200, 120
        cx = w//2 - bw//2
        draw.rectangle([cx, flag_y, cx+bw, flag_y+bh], fill=(255, 255, 255, 255))
        draw.rectangle([cx, flag_y, cx+bw//3, flag_y+bh], fill=(200, 50, 50, 255))
        draw.rectangle([cx+bw//3, flag_y, cx+bw*2//3, flag_y+bh], fill=(50, 200, 50, 255))
        draw.rectangle([cx+bw*2//3, flag_y, cx+bw, flag_y+bh], fill=(50, 50, 200, 255))
        # Texto inferior
        wave = int(10 * (__import__('math').sin(i * 0.3)))
        draw.text((w//2 - 180, flag_y + bh + 30 + wave),
                  "Iniciando...", fill=(200, 255, 200, 255), font=font)
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        frames.append(Image.open(buf).copy())
        buf.close()
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=100, disposal=2)
    return out.getvalue()


def _gen_example_c64():
    """Estilo Commodore 64 (azul con texto)"""
    frames = []
    w, h = 960, 544
    for i in range(25):
        img = Image.new("RGBA", (w, h), (40, 40, 140, 255))
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 160)
            font_s = ImageFont.truetype("arial.ttf", 40)
        except Exception:
            font = ImageFont.load_default()
            font_s = font
        # Logo C= y texto
        draw.text((w//2 - 120, 60), "C=", fill=(200, 100, 100, 255), font=font)
        draw.text((w//2 - 100, 220), "COMMODORE 64", fill=(200, 200, 100, 255), font=font_s)
        # Lineas de colores estilo C64
        for j in range(8):
            yy = h - 100 + j * 15
            cols = [(255,255,255),(160,160,160),(255,100,100),(100,200,100),
                    (100,100,255),(200,100,200),(255,200,100),(100,200,255)]
            draw.rectangle([100, yy, w-100, yy+8], fill=(*cols[j], 200))
        # Texto inferior parpadeante
        if i % 6 < 3:
            draw.text((w//2 - 100, h - 60), "READY.", fill=(200, 200, 200, 255), font=font_s)
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        frames.append(Image.open(buf).copy())
        buf.close()
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=80, disposal=2)
    return out.getvalue()


# ─── Motor principal de conversion ───────────────────────────────────────

def run_conversion(video_path, opts, log_cb, progress_cb):
    fmt = opts.get("format", "rcf")
    res_key = opts.get("resolution", "960x544 (Full Vita)")
    res = RESOLUCIONES.get(res_key, (960, 544))
    fps = opts.get("fps")
    if fps == "auto" or fps is None:
        fps = None
    else:
        fps = int(fps)
    compression = int(opts.get("compression", 6))
    end_logo_path = opts.get("end_logo")

    flags = {
        "loop": opts.get("loop", True),
        "cache": opts.get("cache", False),
        "swap": opts.get("swap", False),
        "vblank": opts.get("vblank", False),
        "sweep": opts.get("sweep", False),
        "sram": opts.get("sram", False),
        "priority": int(opts.get("priority", 6)),
        "nopreload": opts.get("nopreload", False),
        "slowmode": opts.get("slowmode", False),
    }

    log_cb(f"Formato: {fmt.upper()}")
    log_cb(f"Resolucion: {res[0]}x{res[1]}")
    log_cb(f"Compresion: {compression}")
    log_cb(f"Dependencias: Pillow OK, FFmpeg {'OK' if check_ffmpeg() else 'NO INSTALADO'}")

    ext = os.path.splitext(video_path)[1].lower()
    frames = []
    tmp_dir_to_clean = None

    try:
        if ext == ".gif":
            frames, tmp_dir_to_clean = extract_frames_from_gif(video_path, res[0], res[1], log_cb)
        else:
            if not check_ffmpeg():
                raise ConversionError(
                    "FFmpeg no esta instalado.\n\n"
                    "Para usar videos MP4/AVI/etc necesitas FFmpeg:\n"
                    "  1. Descarga: https://ffmpeg.org/download.html\n"
                    "  2. Extrae el .zip y agrega la carpeta bin/ al PATH\n"
                    "  3. O usa un archivo GIF (no necesita FFmpeg)\n\n"
                    "Opcion rapida: convierte tu video a GIF online y usalo aqui."
                )
            frames, tmp_dir_to_clean = extract_frames_from_video(
                video_path, res[0], res[1], fps, log_cb
            )

        if not frames:
            raise ConversionError("No se pudieron extraer frames")

        end_logo_data = None
        if end_logo_path and os.path.exists(end_logo_path):
            log_cb("Procesando logo final...")
            end_logo_data = process_frame_for_logo(end_logo_path, res[0], res[1])

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        convert_frames(frames, OUTPUT_DIR, fmt, res[0], res[1],
                       compression, flags, end_logo_data, log_cb)

        # Auto-copiar a directorio Vita SD si detectado
        for vita_dir in VITA_SD_DIRS:
            vita_anim_dir = vita_dir  # ya incluye PSVitaBootAnim
            try:
                os.makedirs(vita_anim_dir, exist_ok=True)
                for fname in ("boot.rcf", "boot_animation.img"):
                    src = os.path.join(OUTPUT_DIR, fname)
                    if os.path.exists(src):
                        import shutil
                        shutil.copy2(src, os.path.join(vita_anim_dir, fname))
                        log_cb(f"Copiado a {vita_anim_dir}/")
            except (OSError, PermissionError):
                pass

        result = {"ok": True, "files": []}
        rcf_path = os.path.join(OUTPUT_DIR, "boot.rcf")
        cbs_path = os.path.join(OUTPUT_DIR, "boot_animation.img")
        if os.path.exists(rcf_path):
            result["files"].append({"name": "boot.rcf", "size": _fmt_size(os.path.getsize(rcf_path))})
        if os.path.exists(cbs_path):
            result["files"].append({"name": "boot_animation.img", "size": _fmt_size(os.path.getsize(cbs_path))})
        progress_cb(100)
        log_cb("LISTO! Archivos generados en output/ y copiados a Vita SD si disponible")
        return result

    finally:
        if tmp_dir_to_clean:
            shutil.rmtree(tmp_dir_to_clean, ignore_errors=True)


# ─── Interfaz Web (http.server) ──────────────────────────────────────────

HOST = "127.0.0.1"
PORT = 8080
job = {"running": False, "log": [], "progress": 0, "result": None, "video_path": None, "file_name": ""}


class WebUIHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _file(self, path, mime):
        if not os.path.exists(path):
            return self._json({"error": "Archivo no encontrado"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_FILE_SIZE:
            raise ConversionError("Archivo demasiado grande (>300 MB)")
        return self.rfile.read(length)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS, PUT")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/": return self._html()
        if p == "/api/check-deps": return self._json(check_deps())
        if p == "/api/status": return self._json({
            "running": job["running"], "log": job["log"][-200:],
            "progress": job["progress"], "result": job["result"]
        })
        if p == "/api/reset":
            job["result"] = None
            return self._json({"ok": True})
        if p == "/api/download-rcf":
            return self._file(os.path.join(OUTPUT_DIR, "boot.rcf"), "application/octet-stream")
        if p == "/api/download-cbs":
            return self._file(os.path.join(OUTPUT_DIR, "boot_animation.img"), "application/octet-stream")
        if p.startswith("/api/examples/"):
            name = p.split("/api/examples/")[1]
            return self._serve_example(name)
        self._json({"error": "Not found"}, 404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/api/start-upload":
            uid = uuid.uuid4().hex
            os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)
            return self._json({"upload_id": uid})
        if p == "/api/convert":
            return self._handle_convert()
        if p == "/api/ftp-upload":
            return self._handle_ftp_upload()
        if p == "/api/ftp-scan":
            return self._handle_ftp_scan()
        self._json({"error": "Not found"}, 404)

    def do_PUT(self):
        p = urllib.parse.urlparse(self.path).path
        if p.startswith("/api/upload/"):
            return self._handle_upload(p.split("/api/upload/")[1])
        self._json({"error": "Not found"}, 404)

    def _html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(HTML_UI.encode("utf-8"))

    def _handle_upload(self, upload_id):
        try:
            body = self._body()
            ct = self.headers.get("Content-Type", "")
            ext_map = {
                "video/mp4": ".mp4", "video/x-msvideo": ".avi",
                "video/quicktime": ".mov", "video/x-matroska": ".mkv",
                "video/webm": ".webm", "image/gif": ".gif",
                "video/x-ms-wmv": ".wmv", "video/mpeg": ".mpeg",
            }
            ext = ".mp4"
            for k, v in ext_map.items():
                if k in ct:
                    ext = v
                    break
            # Detectar por firma si no hay Content-Type
            if ext == ".mp4" and len(body) > 4:
                if body[:3] == b"GIF":
                    ext = ".gif"
                elif body[:4] == b"\x1aE\xdf\xa3":
                    ext = ".mkv"
                elif body[:4] == b"RIFF":
                    ext = ".avi"

            os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)
            path = os.path.join(TMP_UPLOAD_DIR, f"upload_{upload_id}{ext}")
            with open(path, "wb") as f:
                f.write(body)
            self._json({"ok": True, "upload_id": upload_id, "path": path, "size": len(body)})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _handle_convert(self):
        global job
        if job["running"]:
            return self._json({"error": "Ya hay una conversion en curso"}, 409)
        try:
            data = json.loads(self._body().decode("utf-8"))
        except Exception as e:
            return self._json({"error": f"JSON invalido: {e}"}, 400)

        vpath = data.get("video_path")
        if not vpath or not os.path.exists(vpath):
            return self._json({"error": "Video no encontrado. Subelo primero."}, 400)

        job["log"] = [f"Video: {os.path.basename(vpath)}"]
        job["progress"] = 0
        job["running"] = True
        job["result"] = None

        opts = data.get("options", {})
        t = threading.Thread(target=_bg_convert, args=(vpath, opts), daemon=True)
        t.start()
        self._json({"ok": True})

    def _serve_example(self, name):
        examples = {
            "ps1": ("PS1 Boot", _gen_example_ps1),
            "gameboy": ("GameBoy Boot", _gen_example_gameboy),
            "nintendo": ("Nintendo Switch", _gen_example_switch),
            "dreamcast": ("Dreamcast", _gen_example_dreamcast),
            "snes": ("SNES", _gen_example_snes),
            "gamecube": ("GameCube", _gen_example_gamecube),
            "ps2": ("PS2", _gen_example_ps2),
            "sega": ("SEGA Genesis", _gen_example_sega),
            "wii": ("Nintendo Wii", _gen_example_wii),
            "win95": ("Windows 95", _gen_example_win95),
            "c64": ("Commodore 64", _gen_example_c64),
        }
        if name in examples:
            try:
                label, gen_func = examples[name]
                data = gen_func()
                uid = uuid.uuid4().hex
                os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)
                path = os.path.join(TMP_UPLOAD_DIR, f"example_{uid}.gif")
                with open(path, "wb") as f:
                    f.write(data)
                self._json({"ok": True, "path": path, "name": label, "size": len(data)})
            except Exception as e:
                self._json({"error": f"Error al generar ejemplo: {e}"}, 500)
        else:
            self._json({"error": "Ejemplo no encontrado"}, 404)

    def _handle_ftp_upload(self):
        try:
            data = json.loads(self._body().decode("utf-8"))
        except Exception as e:
            return self._json({"error": f"JSON invalido: {e}"}, 400)
        file_path = data.get("file_path")
        ip = data.get("ip", "192.168.1.100")
        port = int(data.get("port", 1337))
        target = data.get("target", "enso")
        # Resolver ruta virtual
        if file_path == "/api/download-rcf":
            file_path = os.path.join(OUTPUT_DIR, "boot.rcf")
        elif file_path == "/api/download-cbs":
            file_path = os.path.join(OUTPUT_DIR, "boot_animation.img")
        if not file_path or not os.path.exists(file_path):
            return self._json({"error": "Archivo no encontrado. Convierte una animacion primero."}, 400)
        try:
            _ftp_upload_file(file_path, ip, port, target)
            self._json({"ok": True})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _handle_ftp_scan(self):
        found = []
        local_ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            base = ".".join(local_ip.split(".")[:3])
            lock = threading.Lock()
            def scan(ip):
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(0.3)
                    if s.connect_ex((ip, 1337)) == 0:
                        with lock:
                            found.append(ip)
                    s.close()
                except:
                    pass
            threads = []
            for i in range(1, 255):
                t = threading.Thread(target=scan, args=(f"{base}.{i}",), daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join(timeout=3)
        except Exception as e:
            pass
        self._json({"ips": found, "local_ip": local_ip})


def _bg_convert(video_path, opts):
    global job
    def log(msg):
        job["log"].append(str(msg))
    def progress(val):
        job["progress"] = val
    try:
        job["result"] = run_conversion(video_path, opts, log, progress)
    except ConversionError as e:
        job["log"].append(f"ERROR: {e}")
        job["result"] = {"ok": False, "error": str(e)}
    except subprocess.TimeoutExpired:
        job["log"].append("ERROR: La conversion tardó demasiado")
        job["result"] = {"ok": False, "error": "Timeout"}
    except Exception as e:
        job["log"].append(f"ERROR inesperado: {e}")
        import traceback
        job["log"].append(traceback.format_exc())
        job["result"] = {"ok": False, "error": str(e)}
    finally:
        job["running"] = False
        try:
            if video_path and os.path.exists(video_path):
                os.unlink(video_path)
        except Exception:
            pass


# ─── Subida FTP a PS Vita ────────────────────────────────────────────────

def _ftp_upload_file(file_path, ip, port, target):
    ftp = ftplib.FTP()
    ftp.connect(ip, port, timeout=10)
    ftp.login("anonymous", "")
    if target == "enso":
        ftp.cwd("/")
        try:
            ftp.cwd("ur0:/tai")
        except Exception:
            try:
                ftp.cwd("ur0/tai")
            except Exception:
                ftp.cwd("/")
                ftp.cwd("tai")
        dest_name = "boot_splash.rcf"
    else:
        try:
            ftp.cwd("ux0:/data/PSP2CBS")
        except Exception:
            try:
                ftp.cwd("data/PSP2CBS")
            except Exception:
                ftp.cwd("/")
                ftp.cwd("PSP2CBS")
        dest_name = "custom1.cbs"
    with open(file_path, "rb") as f:
        ftp.storbinary(f"STOR {dest_name}", f)
    ftp.quit()


# ─── Interfaz de escritorio (tkinter) ────────────────────────────────────

def run_desktop_mode():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        print("tkinter no disponible en este sistema. Usa el modo web (sin argumentos).")
        sys.exit(1)

    if not check_deps()["pillow"]:
        print("ERROR: Pillow no instalado. pip install Pillow")
        sys.exit(1)

    root = tk.Tk()
    root.title("PS Vita Boot Animation Creator")
    root.geometry("700x580")
    root.configure(bg="#1a1a2e")

    video_path = tk.StringVar()
    end_logo_path = tk.StringVar()
    resolution = tk.StringVar(value="960x544 (Full Vita)")
    fmt_var = tk.StringVar(value="rcf")
    fps_var = tk.StringVar(value="auto")
    compression = tk.IntVar(value=6)
    priority = tk.IntVar(value=6)
    flag_loop = tk.BooleanVar(value=True)
    flag_cache = tk.BooleanVar(value=False)
    flag_swap = tk.BooleanVar(value=False)
    flag_vblank = tk.BooleanVar(value=False)
    flag_sweep = tk.BooleanVar(value=False)
    flag_sram = tk.BooleanVar(value=False)

    log_text = None
    running = False

    def log(msg, color=None):
        if log_text:
            log_text.insert(tk.END, msg + "\n")
            log_text.see(tk.END)
            root.update()

    def browse_video():
        f = filedialog.askopenfilename(
            title="Seleccionar video/GIF",
            filetypes=[("Video/GIF", "*.mp4 *.avi *.mov *.mkv *.webm *.gif"), ("Todos", "*.*")]
        )
        if f:
            video_path.set(f)
            log(f"Video: {os.path.basename(f)} ({_fmt_size(os.path.getsize(f))})")

    def browse_logo():
        f = filedialog.askopenfilename(
            title="Logo final (png/jpg)",
            filetypes=[("Imagen", "*.png *.jpg *.jpeg"), ("Todos", "*.*")]
        )
        if f:
            end_logo_path.set(f)
            log(f"Logo final: {os.path.basename(f)}")

    def run():
        nonlocal running
        if not video_path.get():
            messagebox.showwarning("Sin video", "Selecciona un video primero.")
            return
        if running:
            log("Ya hay una conversion en curso")
            return

        running = True
        if log_text:
            log_text.delete(1.0, tk.END)
        log("=== Iniciando conversion ===")

        def bg():
            nonlocal running
            try:
                opts = {
                    "format": fmt_var.get(),
                    "resolution": resolution.get(),
                    "fps": fps_var.get(),
                    "compression": compression.get(),
                    "priority": priority.get(),
                    "loop": flag_loop.get(),
                    "cache": flag_cache.get(),
                    "swap": flag_swap.get(),
                    "vblank": flag_vblank.get(),
                    "sweep": flag_sweep.get(),
                    "sram": flag_sram.get(),
                    "end_logo": end_logo_path.get() or None,
                }
                result = run_conversion(video_path.get(), opts, log, lambda v: None)
                if result and result.get("ok"):
                    log("LISTO! Archivos en la carpeta output/")
                else:
                    log(f"ERROR: {result}")
            except Exception as e:
                log(f"ERROR: {e}")
            finally:
                running = False

        threading.Thread(target=bg, daemon=True).start()

    # UI
    style = ttk.Style()
    style.theme_use("clam")

    main = ttk.Frame(root, padding="12")
    main.pack(fill=tk.BOTH, expand=True)

    # Video
    f1 = ttk.LabelFrame(main, text="Video de entrada", padding="8")
    f1.pack(fill=tk.X, pady=4)
    r1 = ttk.Frame(f1)
    r1.pack(fill=tk.X)
    ttk.Button(r1, text="Seleccionar video/GIF", command=browse_video).pack(side=tk.LEFT, padx=2)
    ttk.Label(r1, textvariable=video_path, foreground="#e94560").pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)
    ttk.Button(f1, text="Logo final (opcional)", command=browse_logo).pack(anchor=tk.W, pady=2)
    ttk.Label(f1, textvariable=end_logo_path, foreground="#a0a0b0", font=("", 9)).pack(anchor=tk.W)

    # Config
    f2 = ttk.LabelFrame(main, text="Configuracion", padding="8")
    f2.pack(fill=tk.X, pady=4)

    grid = ttk.Frame(f2)
    grid.pack(fill=tk.X)
    ttk.Label(grid, text="Resolucion:").grid(row=0, column=0, sticky=tk.W, padx=4)
    ttk.Combobox(grid, textvariable=resolution, values=list(RESOLUCIONES.keys()),
                 state="readonly", width=30).grid(row=0, column=1, sticky=tk.W, padx=4)
    ttk.Label(grid, text="Formato:").grid(row=0, column=2, sticky=tk.W, padx=8)
    fmt_cb = ttk.Combobox(grid, textvariable=fmt_var, values=["rcf", "cbs", "both"],
                          state="readonly", width=8)
    fmt_cb.grid(row=0, column=3, sticky=tk.W)

    ttk.Label(grid, text="FPS:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=4)
    ttk.Combobox(grid, textvariable=fps_var, values=["auto", "10", "15", "24", "30", "60"],
                 state="readonly", width=8).grid(row=1, column=1, sticky=tk.W, padx=4, pady=4)
    ttk.Label(grid, text="Compresion:").grid(row=1, column=2, sticky=tk.W, padx=8, pady=4)
    ttk.Scale(grid, from_=1, to=9, variable=compression, orient=tk.HORIZONTAL,
              length=100).grid(row=1, column=3, sticky=tk.W, pady=4)
    ttk.Label(grid, textvariable=compression, width=3).grid(row=1, column=4, sticky=tk.W)

    # Flags
    f3 = ttk.LabelFrame(main, text="Flags RCF", padding="8")
    f3.pack(fill=tk.X, pady=4)
    fg = ttk.Frame(f3)
    fg.pack(fill=tk.X)
    col = 0
    for text, var in [("Loop", flag_loop), ("Cache", flag_cache), ("Swap FB", flag_swap),
                      ("VBlank", flag_vblank), ("Wipe", flag_sweep), ("SRAM", flag_sram)]:
        ttk.Checkbutton(fg, text=text, variable=var).grid(row=0, column=col, sticky=tk.W, padx=6)
        col += 1
    ttk.Label(f3, text="Prioridad:").pack(side=tk.LEFT, padx=4)
    ttk.Scale(f3, from_=0, to=255, variable=priority, orient=tk.HORIZONTAL,
              length=150).pack(side=tk.LEFT, padx=4)
    ttk.Label(f3, textvariable=priority, width=3).pack(side=tk.LEFT)
    ttk.Label(f3, text="(6-10 recomendado)", font=("", 8)).pack(side=tk.LEFT, padx=4)

    # Log
    f4 = ttk.LabelFrame(main, text="Log", padding="4")
    f4.pack(fill=tk.BOTH, expand=True, pady=4)
    sb = ttk.Scrollbar(f4)
    sb.pack(side=tk.RIGHT, fill=tk.Y)
    log_text = tk.Text(f4, height=12, yscrollcommand=sb.set,
                       font=("Consolas", 9), bg="#0d0d1a", fg="#e8e8e8",
                       insertbackground="white", state=tk.NORMAL)
    log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    sb.config(command=log_text.yview)
    log_text.insert(tk.END, "Listo. Selecciona un video y presiona Iniciar.\n")
    log_text.insert(tk.END, "Sugerencia: usa GIF si no tienes FFmpeg instalado.\n")

    # Boton
    btn_frame = ttk.Frame(main)
    btn_frame.pack(fill=tk.X, pady=6)
    ttk.Button(btn_frame, text="INICIAR CONVERSION", command=run).pack(side=tk.RIGHT, padx=4)
    ttk.Button(btn_frame, text="Abrir carpeta output",
               command=lambda: os.startfile(OUTPUT_DIR) if os.path.exists(OUTPUT_DIR) else None
              ).pack(side=tk.RIGHT, padx=4)

    root.mainloop()


# ─── HTML / CSS / JS (Interfaz web mejorada) ─────────────────────────────

HTML_UI = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PS Vita Boot Animation Creator</title>
<style>
  :root { --bg: #1a1a2e; --surface: #16213e; --surface2: #1f3050; --primary: #e94560; --primary-dim: #a83245; --accent: #0f3460; --text: #e8e8e8; --text-dim: #a0a0b0; --success: #2ecc71; --warn: #f39c12; --error: #e74c3c; --radius: 10px; --shadow: 0 4px 20px rgba(0,0,0,0.3); }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .header { background: linear-gradient(135deg, var(--surface), var(--accent)); padding: 20px 28px; box-shadow: var(--shadow); display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 20px; font-weight: 700; }
  .header .subtitle { color: var(--text-dim); font-size: 12px; margin-top: 2px; }
  .header .logo { font-size: 28px; }
  .container { max-width: 960px; margin: 0 auto; padding: 20px; }
  .card { background: var(--surface); border-radius: var(--radius); padding: 20px; margin-bottom: 12px; box-shadow: var(--shadow); }
  .card-title { font-size: 13px; font-weight: 600; margin-bottom: 12px; color: var(--primary); text-transform: uppercase; letter-spacing: 1px; }
  .drop-zone { border: 2px dashed var(--text-dim); border-radius: var(--radius); padding: 30px; text-align: center; cursor: pointer; transition: all .3s; }
  .drop-zone:hover, .drop-zone.dragover { border-color: var(--primary); background: rgba(233,69,96,0.05); }
  .drop-zone .icon { font-size: 36px; margin-bottom: 8px; }
  .drop-zone .text { color: var(--text-dim); font-size: 14px; }
  .drop-zone .subtext { font-size: 11px; color: var(--text-dim); margin-top: 4px; }
  .drop-zone .filename { font-size: 13px; color: var(--primary); font-weight: 600; margin-top: 8px; display: none; }
  .drop-zone.has-file { border-color: var(--success); }
  .drop-zone.has-file .filename { display: block; }
  .drop-zone input[type=file] { display: none; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .form-group { margin-bottom: 10px; }
  .form-group label { display: block; font-size: 11px; color: var(--text-dim); margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.5px; }
  .form-group select, .form-group input { width: 100%; padding: 6px 10px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.1); background: var(--surface2); color: var(--text); font-size: 12px; outline: none; transition: border .2s; }
  .form-group select:focus, .form-group input:focus { border-color: var(--primary); }
  .form-group select option { background: var(--surface2); }
  .radio-group { display: flex; gap: 6px; flex-wrap: wrap; }
  .radio-group label { display: flex; align-items: center; gap: 4px; padding: 4px 12px; border-radius: 16px; background: var(--surface2); cursor: pointer; font-size: 12px; transition: all .2s; border: 1px solid transparent; }
  .radio-group label:hover { border-color: var(--primary-dim); }
  .radio-group input:checked + span { color: var(--primary); font-weight: 600; }
  .radio-group input[type=radio] { display: none; }
  .radio-group label:has(input:checked) { border-color: var(--primary); background: rgba(233,69,96,0.1); }
  .toggle-grid { display: flex; flex-wrap: wrap; gap: 4px; }
  .toggle-btn { padding: 3px 10px; border-radius: 12px; background: var(--surface2); cursor: pointer; font-size: 11px; user-select: none; transition: all .2s; border: 1px solid transparent; }
  .toggle-btn.active { border-color: var(--primary); background: rgba(233,69,96,0.15); color: var(--primary); }
  .toggle-btn input { display: none; }
  .log-box { background: #0d0d1a; border-radius: var(--radius); padding: 12px; height: 200px; overflow-y: auto; font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 11px; line-height: 1.5; border: 1px solid rgba(255,255,255,0.05); }
  .log-error { color: var(--error); } .log-warn { color: var(--warn); } .log-success { color: var(--success); } .log-info { color: var(--text-dim); }
  .progress-bar { height: 3px; background: var(--surface2); border-radius: 3px; margin: 8px 0; overflow: hidden; display: none; }
  .progress-bar.active { display: block; }
  .progress-bar .fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--primary), var(--success)); border-radius: 3px; transition: width .3s; }
  .btn { padding: 8px 22px; border-radius: 6px; border: none; font-weight: 600; font-size: 13px; cursor: pointer; transition: all .2s; display: inline-flex; align-items: center; gap: 6px; }
  .btn-primary { background: var(--primary); color: #fff; }
  .btn-primary:hover { background: var(--primary-dim); transform: translateY(-1px); }
  .btn-primary:disabled { opacity: .4; cursor: not-allowed; transform: none; }
  .btn-success { background: var(--success); color: #fff; }
  .btn-success:hover { opacity: .9; transform: translateY(-1px); }
  .btn-outline { background: transparent; border: 1px solid var(--text-dim); color: var(--text); padding: 6px 16px; }
  .btn-outline:hover { border-color: var(--text); }
  .btn-sm { padding: 4px 12px; font-size: 11px; }
  .actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .deps { display: flex; gap: 16px; font-size: 12px; align-items: center; }
  .dep-item { display: flex; align-items: center; gap: 4px; }
  .dep-item .dot { width: 7px; height: 7px; border-radius: 50%; }
  .dep-item .dot.ok { background: var(--success); }
  .dep-item .dot.missing { background: var(--error); }
  .result-box { display: none; }
  .result-box.show { display: block; }
  .result-box .file-item { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: var(--surface2); border-radius: 6px; margin-bottom: 6px; }
  .result-box .file-item .name { font-weight: 600; font-size: 12px; }
  .result-box .file-item .size { color: var(--text-dim); font-size: 11px; }
  .install-note { background: rgba(46,204,113,0.1); border: 1px solid rgba(46,204,113,0.3); border-radius: var(--radius); padding: 12px; margin-top: 10px; font-size: 12px; line-height: 1.6; }
  .install-note code { background: rgba(0,0,0,0.3); padding: 1px 5px; border-radius: 3px; font-size: 11px; }
  .example-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .example-card { background: var(--surface2); border-radius: 8px; padding: 12px; text-align: center; cursor: pointer; transition: all .2s; border: 1px solid transparent; }
  .example-card:hover { border-color: var(--primary); transform: translateY(-2px); }
  .example-card .preview { font-size: 32px; margin-bottom: 6px; }
  .example-card .name { font-size: 12px; font-weight: 600; }
  .example-card .desc { font-size: 10px; color: var(--text-dim); }
  ::-webkit-scrollbar { width: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--surface2); border-radius: 3px; }
  @media (max-width: 640px) { .grid-2, .example-grid { grid-template-columns: 1fr; } }
  .logo-upload { margin-top: 8px; display: flex; align-items: center; gap: 8px; }
  .logo-upload label { font-size: 11px; color: var(--text-dim); cursor: pointer; padding: 4px 12px; border-radius: 12px; background: var(--surface2); }
  .logo-upload label:hover { background: var(--primary-dim); }
  .logo-upload #logoName { font-size: 11px; color: var(--text-dim); }
  .ffmpeg-guide { background: rgba(243,156,18,0.1); border: 1px solid rgba(243,156,18,0.3); border-radius: var(--radius); padding: 12px; margin-top: 8px; font-size: 12px; line-height: 1.6; display: none; }
  .ffmpeg-guide.show { display: block; }
</style>
</head>
<body>
<div class="header">
  <div class="logo">PSV</div>
  <div>
    <h1>PS Vita Boot Animation Creator</h1>
    <div class="subtitle">Crea animaciones de arranque personalizadas - GIF sin FFmpeg</div>
  </div>
</div>
<div class="container">
  <div class="card" style="padding: 10px 16px;">
    <div class="deps">
      <span class="dep-item"><span class="dot" id="dep-pillow"></span> Pillow</span>
      <span class="dep-item"><span class="dot" id="dep-ffmpeg"></span> FFmpeg</span>
      <span style="font-size:10px;color:var(--text-dim);" id="dep-ffmpeg-ver"></span>
      <span id="statusBadge" style="margin-left:auto;font-size:11px;"></span>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Video de entrada</div>
    <div class="drop-zone" id="dropZone">
      <div class="icon">+</div>
      <div class="text">Arrastra un video o GIF aqui</div>
      <div class="subtext">MP4, AVI, MOV, WEBM, GIF - Los GIF funcionan sin FFmpeg</div>
      <div class="filename" id="fileName"></div>
      <input type="file" id="fileInput" accept="video/*,.gif">
    </div>
    <div class="logo-upload">
      <label for="logoInput">+ Logo final (opcional, 960x544)</label>
      <input type="file" id="logoInput" accept="image/*" style="display:none">
      <span id="logoName"></span>
    </div>
  </div>

  <div class="card" id="ffmpegWarning" style="display:none;">
    <div class="ffmpeg-guide show" id="ffmpegGuide">
      <strong>FFmpeg no detectado.</strong> Los videos MP4/AVI/etc necesitan FFmpeg.<br>
      <strong>Soluciones:</strong><br>
      1. <a href="https://ffmpeg.org/download.html" target="_blank" style="color:var(--primary);">Descarga FFmpeg</a> y agrega la carpeta bin/ al PATH<br>
      2. O usa un <strong>GIF</strong> (no necesita FFmpeg - funciona con Pillow)<br>
      3. Convierte tu video a GIF online (ej: ezgif.com) y usalo aqui
    </div>
  </div>

  <div class="card">
    <div class="card-title">Ejemplos rapidos</div>
    <div class="example-grid" id="exampleGrid">
      <div class="example-card" onclick="loadExample('ps1')">
        <div class="preview">🕹️</div>
        <div class="name">PS1 Boot</div>
        <div class="desc">PlayStation 1 startup</div>
      </div>
      <div class="example-card" onclick="loadExample('ps2')">
        <div class="preview">🎮</div>
        <div class="name">PS2</div>
        <div class="desc">PlayStation 2 boot</div>
      </div>
      <div class="example-card" onclick="loadExample('dreamcast')">
        <div class="preview">🌀</div>
        <div class="name">Dreamcast</div>
        <div class="desc">SEGA Dreamcast swirl</div>
      </div>
      <div class="example-card" onclick="loadExample('sega')">
        <div class="preview">💙</div>
        <div class="name">SEGA Genesis</div>
        <div class="desc">SEGA boot screen</div>
      </div>
      <div class="example-card" onclick="loadExample('snes')">
        <div class="preview">🔲</div>
        <div class="name">SNES</div>
        <div class="desc">Super Nintendo boot</div>
      </div>
      <div class="example-card" onclick="loadExample('gamecube')">
        <div class="preview">🎯</div>
        <div class="name">GameCube</div>
        <div class="desc">Nintendo GameCube</div>
      </div>
      <div class="example-card" onclick="loadExample('wii')">
        <div class="preview">📡</div>
        <div class="name">Nintendo Wii</div>
        <div class="desc">Wii boot animation</div>
      </div>
      <div class="example-card" onclick="loadExample('gameboy')">
        <div class="preview">🎮</div>
        <div class="name">GameBoy</div>
        <div class="desc">Game Boy boot screen</div>
      </div>
      <div class="example-card" onclick="loadExample('nintendo')">
        <div class="preview">🔄</div>
        <div class="name">Switch</div>
        <div class="desc">Nintendo Switch</div>
      </div>
      <div class="example-card" onclick="loadExample('win95')">
        <div class="preview">🪟</div>
        <div class="name">Windows 95</div>
        <div class="desc">Classic Windows boot</div>
      </div>
      <div class="example-card" onclick="loadExample('c64')">
        <div class="preview">💾</div>
        <div class="name">Commodore 64</div>
        <div class="desc">C64 startup</div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Configuracion</div>
    <div class="grid-2">
      <div class="form-group">
        <label>Resolucion</label>
        <select id="resolution">
          <option value="960x544 (Full Vita)">960x544 (Full Vita)</option>
          <option value="768x408">768x408</option>
          <option value="640x368">640x368</option>
          <option value="512x272 (Mapped 480x272)">512x272 (Mapped 480x272)</option>
          <option value="480x272 (PSP-like)">480x272 (PSP-like)</option>
        </select>
      </div>
      <div class="form-group">
        <label>FPS</label>
        <select id="fps">
          <option value="auto">Auto</option>
          <option value="10">10</option>
          <option value="15">15</option>
          <option value="24">24</option>
          <option value="30">30</option>
          <option value="60">60</option>
        </select>
      </div>
    </div>
    <div class="form-group">
      <label>Formato</label>
      <div class="radio-group">
        <label><input type="radio" name="format" value="rcf" checked><span>RCF (recomendado)</span></label>
        <label><input type="radio" name="format" value="cbs"><span>IMG (CBS)</span></label>
        <label><input type="radio" name="format" value="both"><span>Ambos</span></label>
      </div>
    </div>
    <div class="form-group">
      <label>Compresion <span id="compVal">6</span>/9</label>
      <input type="range" id="compression" min="1" max="9" value="6" style="width:100%;accent-color:var(--primary);">
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-dim);"><span>1 (rapido)</span><span>9 (maximo)</span></div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Flags avanzados (RCF)</div>
    <div class="grid-2">
      <div class="toggle-grid" id="flagContainer">
        <label class="toggle-btn active" data-flag="loop"><input type="checkbox" checked> Loop</label>
        <label class="toggle-btn" data-flag="cache"><input type="checkbox"> Cache</label>
        <label class="toggle-btn" data-flag="swap"><input type="checkbox"> Swap FB</label>
        <label class="toggle-btn" data-flag="vblank"><input type="checkbox"> VBlank</label>
        <label class="toggle-btn" data-flag="sweep"><input type="checkbox"> Wipe</label>
        <label class="toggle-btn" data-flag="sram"><input type="checkbox"> SRAM</label>
      </div>
      <div>
        <label style="font-size:11px;color:var(--text-dim);">Prioridad hilo (0-255)</label>
        <div style="display:flex;align-items:center;gap:6px;">
          <input type="number" id="priority" min="0" max="255" value="6" style="width:60px;">
          <span style="font-size:10px;color:var(--text-dim);">(6-10 recomendado)</span>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Salida</div>
    <div class="progress-bar" id="progressBar"><div class="fill" id="progressFill"></div></div>
    <div class="log-box" id="logBox"><span class="log-info">Listo. Arrastra un video o GIF para empezar.</span></div>
    <div class="actions" style="margin-top:8px;">
      <button class="btn btn-primary" id="btnConvert" onclick="startConversion()">Crear animacion</button>
      <button class="btn btn-outline btn-sm" onclick="clearLog()">Limpiar</button>
      <button class="btn btn-outline btn-sm" onclick="openOutput()">Abrir output</button>
      <span id="statusText" style="font-size:12px;color:var(--text-dim);"></span>
    </div>
    <div class="result-box" id="resultBox">
      <div style="margin-top:12px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.1);">
        <div style="font-size:13px;font-weight:600;color:var(--success);margin-bottom:8px;">Conversion completada</div>
        <div id="fileList"></div>
        <div class="install-note">
          <strong>Instalacion en PS Vita:</strong><br>
          1. Copia los archivos a <code>ur0:tai/</code> via VitaShell (USB/FTP)<br>
          2. RCF: Ajustes &gt; Tema y fondo &gt; Animacion de inicio<br>
          3. IMG: Usa CBS-Manager o enso_ex<br>
          4. Si algo falla, mantén L al iniciar para saltar
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Subir a PS Vita (FTP)</div>
    <div class="grid-2">
      <div class="form-group">
        <label>IP de la Vita</label>
        <div style="display:flex;gap:4px;">
          <input type="text" id="vitaIp" class="input" value="192.168.1.100" placeholder="192.168.1.100" style="flex:1;">
          <button class="btn btn-outline btn-sm" onclick="scanVita()" title="Escanear red local">🔍</button>
        </div>
      </div>
      <div class="form-group">
        <label>Puerto FTP</label>
        <input type="number" id="vitaPort" class="input" value="1337">
      </div>
    </div>
    <div class="form-group">
      <label>Destino</label>
      <select id="vitaTarget" class="input">
        <option value="enso">Enso Ex (ur0:tai/boot_splash.rcf)</option>
        <option value="cbs">CBS Manager (ux0:data/PSP2CBS/custom1.cbs)</option>
      </select>
    </div>
    <div class="form-group">
      <button class="btn btn-primary" onclick="uploadToVita()">Subir a PS Vita</button>
      <span id="ftpStatus" style="font-size:12px;color:var(--text-dim);margin-left:8px;"></span>
    </div>
  </div>

</div>
<script>
let selectedFile = null, logoFile = null, pollInterval = null, uploadId = null, videoPath = null, hasFfmpeg = false;

fetch("/api/check-deps").then(r=>r.json()).then(d=>{
  document.getElementById("dep-pillow").className = "dot " + (d.pillow?"ok":"missing");
  document.getElementById("dep-ffmpeg").className = "dot " + (d.ffmpeg?"ok":"missing");
  hasFfmpeg = d.ffmpeg;
  if (d.ffmpeg_version) {
    let v = d.ffmpeg_version.replace("ffmpeg version ","").split(" ")[0];
    document.getElementById("dep-ffmpeg-ver").textContent = v;
  }
  document.getElementById("statusBadge").textContent = d.pillow ? "Servidor OK" : "Falta Pillow";
  if (!d.ffmpeg) document.getElementById("ffmpegWarning").style.display = "block";
});

const dz = document.getElementById("dropZone"), fi = document.getElementById("fileInput");
dz.addEventListener("click", () => fi.click());
dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("dragover"); });
dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
dz.addEventListener("drop", e => { e.preventDefault(); dz.classList.remove("dragover"); if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]); });
fi.addEventListener("change", e => { if (e.target.files.length) handleFile(e.target.files[0]); });
document.getElementById("logoInput").addEventListener("change", e => {
  if (e.target.files.length) { logoFile = e.target.files[0]; document.getElementById("logoName").textContent = logoFile.name; }
});

function handleFile(file) {
  if (file.size > 300*1024*1024) { alert("Max 300 MB"); return; }
  let ext = file.name.split(".").pop().toLowerCase();
  if (!hasFfmpeg && !["gif"].includes(ext)) {
    if (!confirm("FFmpeg no instalado. GIF funciona sin FFmpeg. Otros formatos fallaran. Usar de todas formas?")) return;
  }
  selectedFile = file;
  document.getElementById("fileName").textContent = file.name + " (" + fmtSize(file.size) + ")";
  dz.classList.add("has-file");
  document.getElementById("statusText").textContent = file.name;
}

function fmtSize(n) { for (const u of ["B","KB","MB","GB"]) { if (n < 1024) return n.toFixed(1)+" "+u; n/=1024; } }

document.querySelectorAll(".toggle-btn").forEach(btn => {
  btn.addEventListener("click", () => { btn.querySelector("input").checked = !btn.querySelector("input").checked; btn.classList.toggle("active"); });
});
document.getElementById("compression").addEventListener("input", function() {
  document.getElementById("compVal").textContent = this.value;
});

async function loadExample(name) {
  try {
    let r = await fetch("/api/examples/"+name);
    let d = await r.json();
    if (!d.ok) { alert("Error: "+d.error); return; }
    videoPath = d.path;
    selectedFile = new File([""], d.name+".gif");
    document.getElementById("fileName").textContent = d.name + " (" + fmtSize(d.size) + ")";
    dz.classList.add("has-file");
    document.getElementById("statusText").textContent = d.name + " cargado";
    logMsg("Ejemplo cargado: " + d.name);
  } catch(e) { alert("Error al cargar ejemplo: "+e.message); }
}

async function startConversion() {
  if (!selectedFile && !videoPath) { alert("Selecciona un video primero."); return; }
  const btn = document.getElementById("btnConvert"), lb = document.getElementById("logBox");
  const pb = document.getElementById("progressBar"), pf = document.getElementById("progressFill");
  btn.disabled = true; btn.textContent = "Procesando...";
  lb.innerHTML = ""; pb.classList.add("active"); pf.style.width = "0%";
  document.getElementById("resultBox").classList.remove("show");

  try {
    // Upload
    if (!videoPath) {
      logMsg("Subiendo video...");
      let r = await fetch("/api/start-upload", {method:"POST"});
      let d = await r.json();
      uploadId = d.upload_id;
      r = await fetch("/api/upload/"+uploadId, {
        method:"PUT", body: await selectedFile.arrayBuffer(),
        headers: {"Content-Type": selectedFile.type || "video/mp4"}
      });
      d = await r.json();
      if (!d.ok) { logMsg("Error subida: "+(d.error||"?"),"error"); btn.disabled=false; btn.textContent="Crear animacion"; return; }
      videoPath = d.path;
    }

    btn.textContent = "Convirtiendo...";
    let flags = {};
    document.querySelectorAll(".toggle-btn input").forEach(cb => { flags[cb.closest(".toggle-btn").dataset.flag] = cb.checked; });
    let opts = {
      format: document.querySelector("input[name=format]:checked").value,
      resolution: document.getElementById("resolution").value,
      fps: document.getElementById("fps").value,
      compression: parseInt(document.getElementById("compression").value),
      priority: parseInt(document.getElementById("priority").value),
      ...flags
    };

    // Upload logo si hay
    if (logoFile) {
      let r = await fetch("/api/start-upload", {method:"POST"});
      let d = await r.json();
      r = await fetch("/api/upload/"+d.upload_id, {
        method:"PUT", body: await logoFile.arrayBuffer(),
        headers: {"Content-Type": "image/png"}
      });
      d = await r.json();
      if (d.ok) opts.end_logo = d.path;
    }

    let r = await fetch("/api/convert", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({video_path:videoPath, options:opts})});
    let data = await r.json();
    if (r.status === 409) { logMsg("Ya hay una conversion","warn"); btn.disabled=false; btn.textContent="Crear animacion"; return; }

    pollInterval = setInterval(async () => {
      try {
        let r2 = await fetch("/api/status"), s = await r2.json();
        lb.innerHTML = s.log.map(l => {
          let c = "log-info";
          if (l.includes("ERROR")) c = "log-error";
          else if (l.includes("ADVERTENCIA")) c = "log-warn";
          else if (l.includes("LISTO")) c = "log-success";
          return `<span class="${c}">${escHtml(l)}</span>`;
        }).join("\n");
        lb.scrollTop = lb.scrollHeight;
        pf.style.width = (s.progress||0)+"%";
        if (!s.running) {
          clearInterval(pollInterval); btn.disabled=false; btn.textContent="Crear animacion"; pb.classList.remove("active");
          if (s.result && s.result.ok) showResult(s.result.files);
          else if (s.result) logMsg("Error: "+s.result.error,"error");
        }
      } catch(e) {}
    }, 500);
  } catch(e) { logMsg("Error conexion: "+e.message,"error"); btn.disabled=false; btn.textContent="Crear animacion"; }
}

function showResult(files) {
  document.getElementById("resultBox").classList.add("show");
  const fl = document.getElementById("fileList"); fl.innerHTML = "";
  files.forEach(f => {
    let ext = f.name.endsWith(".rcf")?"rcf":"img";
    fl.innerHTML += '<div class="file-item"><div><div class="name">'+f.name+'</div><div class="size">'+f.size+'</div></div><a href="/api/download-'+ext+'" class="btn btn-success btn-sm" download>Descargar</a></div>';
  });
}

function logMsg(m,t) { let lb=document.getElementById("logBox"),c=t==="error"?"log-error":t==="warn"?"log-warn":"log-info"; lb.innerHTML+='<span class="'+c+'">'+escHtml(m)+'</span>\n'; lb.scrollTop=lb.scrollHeight; }
function escHtml(s) { let d=document.createElement("div"); d.textContent=s; return d.innerHTML; }
function clearLog() { document.getElementById("logBox").innerHTML='<span class="log-info">Log limpiado.</span>'; document.getElementById("resultBox").classList.remove("show"); document.getElementById("progressBar").classList.remove("active"); }
function openOutput() { window.open("/api/download-rcf"); }

async function scanVita() {
  const btn = event.target; btn.disabled = true; btn.textContent = "...";
  document.getElementById("ftpStatus").textContent = "Escaneando red...";
  try {
    let r = await fetch("/api/ftp-scan", {method:"POST"});
    let d = await r.json();
    if (d.ips && d.ips.length > 0) {
      document.getElementById("vitaIp").value = d.ips[0];
      document.getElementById("ftpStatus").textContent = "Vita encontrada: " + d.ips.join(", ");
    } else {
      document.getElementById("ftpStatus").textContent = "No se encontraron Vitas en " + d.local_ip + "/24";
    }
  } catch(e) {
    document.getElementById("ftpStatus").textContent = "Error: " + e.message;
  }
  btn.disabled = false; btn.textContent = "🔍";
}

async function uploadToVita() {
  const ip = document.getElementById("vitaIp").value.trim();
  const port = document.getElementById("vitaPort").value;
  const target = document.getElementById("vitaTarget").value;
  const ext = target === "enso" ? "rcf" : "cbs";
  const statusEl = document.getElementById("ftpStatus");
  statusEl.textContent = "Subiendo...";
  try {
    let r = await fetch("/api/ftp-upload", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        file_path: "/api/download-" + ext,
        ip: ip,
        port: parseInt(port),
        target: target
      })
    });
    let d = await r.json();
    if (d.ok) {
      statusEl.textContent = "LISTO! Archivo subido a la Vita";
      logMsg("Animacion instalada en la Vita via FTP");
    } else {
      statusEl.textContent = "Error: " + d.error;
      logMsg("Error FTP: " + d.error, "error");
    }
  } catch(e) {
    statusEl.textContent = "Error: " + e.message;
    logMsg("Error FTP: " + e.message, "error");
  }
}
</script>
</body>
</html>
"""


# ─── Entry point ─────────────────────────────────────────────────────────

def main():
    global PORT
    import argparse
    ap = argparse.ArgumentParser(description="PS Vita Boot Animation Creator v2")
    ap.add_argument("--port", type=int, default=8080, help="Puerto (default: 8080)")
    ap.add_argument("--desktop", action="store_true", help="Modo escritorio (tkinter)")
    ap.add_argument("--browser", action="store_true", help="Abrir navegador automaticamente")
    args = ap.parse_args()

    deps = check_deps()

    if args.desktop:
        run_desktop_mode()
        return

    if not deps.get("pillow"):
        print("[ERROR] Pillow no instalado. pip install Pillow")
        sys.exit(1)

    PORT = args.port
    url = f"http://{HOST}:{PORT}"
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)

    server = http.server.HTTPServer((HOST, PORT), WebUIHandler)

    if args.browser:
        webbrowser.open(url)

    print("[PS Vita Boot Animation Creator v2]")
    print("=" * 50)
    print(" URL:", url)
    print(" Pillow:", "OK" if deps["pillow"] else "FALTA")
    print(" FFmpeg:", "OK" if deps["ffmpeg"] else "NO (usa GIF)")
    print()
    print(" Ctrl+C para detener.")
    print("=" * 50)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDetenido.")
        server.shutdown()


if __name__ == "__main__":
    main()
