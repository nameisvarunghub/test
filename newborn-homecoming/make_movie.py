#!/usr/bin/env python3
"""
make_movie.py - Turn a folder of photos + raw video clips into one smooth,
projector-ready movie (1080p MP4) with gentle Ken Burns motion on photos,
cross-fades between every shot, an optional title card, and optional music.

Quick start:
    python3 make_movie.py ./media -o welcome_home.mp4 \
        --title "Welcome Home" --subtitle "Baby Aria  ·  November 2026" \
        --music ./lullaby.mp3

Requirements: ffmpeg + ffprobe on PATH, Python 3.8+, Pillow
(`pip install pillow`). For iPhone .HEIC photos also `pip install pillow-heif`.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

try:  # iPhone photos
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    HEIC_OK = True
except Exception:
    HEIC_OK = False

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".mts", ".webm"}

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/Library/Fonts/Georgia.ttf",
    "C:/Windows/Fonts/georgia.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
]


# --------------------------------------------------------------------------- helpers
def run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        sys.stderr.write("\n$ " + " ".join(cmd) + "\n" + res.stderr[-3000:] + "\n")
        raise SystemExit(f"ffmpeg failed (exit {res.returncode})")


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        stdout=subprocess.PIPE, text=True, check=True,
    ).stdout
    return json.loads(out)


def duration_of(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def has_audio(info: dict) -> bool:
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def capture_time(path: Path) -> float:
    """Best guess at when the photo/clip was taken (EXIF / metadata), else file mtime."""
    try:
        if path.suffix.lower() in PHOTO_EXT:
            with Image.open(path) as im:
                exif = im.getexif()
                raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)  # DateTimeOriginal / DateTime
                if raw:
                    return datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S").timestamp()
        else:
            info = probe(path)
            tags = dict(info.get("format", {}).get("tags", {}))
            for s in info.get("streams", []):
                tags.update(s.get("tags", {}) or {})
            for key in ("com.apple.quicktime.creationdate", "creation_time"):
                if key in tags:
                    return datetime.fromisoformat(tags[key].replace("Z", "+00:00")).timestamp()
    except Exception:
        pass
    return path.stat().st_mtime


def load_font(size: int, font_path: str | None) -> ImageFont.FreeTypeFont:
    for p in ([font_path] if font_path else []) + FONT_CANDIDATES:
        if p and os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def cover(im: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.fit(im, (w, h), Image.LANCZOS)


def contain(im: Image.Image, w: int, h: int) -> Image.Image:
    im = im.copy()
    im.thumbnail((w, h), Image.LANCZOS)
    if im.width < w and im.height < h:  # upscale small images too
        r = min(w / im.width, h / im.height)
        im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    return im


def compose_frame(im: Image.Image, w: int, h: int, fill: str) -> Image.Image:
    """Place any-orientation photo on a 16:9 canvas with a soft blurred backdrop."""
    im = ImageOps.exif_transpose(im).convert("RGB")
    if fill == "crop":
        return cover(im, w, h)
    bg = cover(im, w // 8, h // 8).filter(ImageFilter.GaussianBlur(6)).resize((w, h), Image.BILINEAR)
    bg = Image.blend(bg, Image.new("RGB", (w, h), (0, 0, 0)), 0.35)
    fg = contain(im, w, h)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bg


# --------------------------------------------------------------------------- segments
class Builder:
    def __init__(self, a: argparse.Namespace, work: Path):
        self.a = a
        self.work = work
        self.W, self.H = a.width, a.height
        self.n = 0
        self.rng = random.Random(7)

    def _out(self) -> Path:
        self.n += 1
        return self.work / f"seg_{self.n:04d}.mp4"

    def _encode_args(self) -> list[str]:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]

    def still(self, frame: Image.Image, seconds: float) -> Path:
        """Render a still frame with slow Ken Burns zoom/pan."""
        src = self.work / f"still_{self.n + 1:04d}.png"
        # render at 2x so zoompan motion is smooth (no jitter)
        frame.resize((self.W * 2, self.H * 2), Image.LANCZOS).save(src)
        fps, frames = self.a.fps, max(1, round(seconds * self.a.fps))
        z = self.a.zoom
        if self.a.no_motion:
            zexpr, x, y = "1", "0", "0"
        else:
            zoom_in = self.rng.random() < 0.6
            zexpr = f"1+{z}*on/{frames}" if zoom_in else f"{1 + z}-{z}*on/{frames}"
            px, py = self.rng.choice([(0.5, 0.5), (0.3, 0.5), (0.7, 0.5), (0.5, 0.35), (0.5, 0.65)])
            x = f"(iw-iw/zoom)*{px}"
            y = f"(ih-ih/zoom)*{py}"
        vf = (f"zoompan=z='{zexpr}':x='{x}':y='{y}':d={frames}:s={self.W}x{self.H}:fps={fps},"
              f"setsar=1,format=yuv420p")
        out = self._out()
        run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(src),
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-vf", vf, "-frames:v", str(frames), "-t", f"{frames / fps:.3f}", "-shortest",
             *self._encode_args(), str(out)])
        return out

    def photo(self, path: Path) -> Path:
        with Image.open(path) as im:
            frame = compose_frame(im, self.W, self.H, self.a.fill)
        return self.still(frame, self.a.photo_seconds)

    def video(self, path: Path) -> Path:
        info = probe(path)
        W, H, fps = self.W, self.H, self.a.fps
        if self.a.fill == "crop":
            v = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                 f"setsar=1,fps={fps},format=yuv420p[v]")
        else:
            v = (f"[0:v]split[a][b];"
                 f"[a]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,crop={W // 8}:{H // 8},"
                 f"gblur=sigma=6,scale={W}:{H},eq=brightness=-0.12[bg];"
                 f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
                 f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},format=yuv420p[v]")
        vol = self.a.clip_volume
        if has_audio(info) and vol > 0:
            aud = f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol}[aout]"
            inputs = ["-i", str(path)]
        else:
            aud = "[1:a]anull[aout]"
            inputs = ["-i", str(path), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        trim = []
        if self.a.max_clip_seconds:
            total = float(info["format"].get("duration", 0) or 0)
            if total > self.a.max_clip_seconds:  # keep the middle of long clips
                start = max(0.0, (total - self.a.max_clip_seconds) / 2)
                trim = ["-ss", f"{start:.2f}"]
                inputs = trim + inputs[:2] + inputs[2:]
        out = self._out()
        cmd = ["ffmpeg", "-y", "-v", "error", *inputs,
               "-filter_complex", f"{v};{aud}", "-map", "[v]", "-map", "[aout]", "-shortest"]
        if self.a.max_clip_seconds:
            cmd += ["-t", str(self.a.max_clip_seconds)]
        run(cmd + [*self._encode_args(), str(out)])
        return out

    def title_card(self, title: str, subtitle: str, backdrop: Path | None, seconds: float) -> Path:
        W, H = self.W, self.H
        if backdrop is not None:
            with Image.open(backdrop) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                bg = cover(im, W // 6, H // 6).filter(ImageFilter.GaussianBlur(4)).resize((W, H), Image.BILINEAR)
            bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.45)
        else:
            bg = Image.new("RGB", (W, H), (24, 22, 30))
        d = ImageDraw.Draw(bg)
        tf = load_font(int(H * 0.11), self.a.font)
        sf = load_font(int(H * 0.04), self.a.font)
        cy = H * (0.45 if subtitle else 0.5)
        for text, font, y, fill in ((title, tf, cy, (255, 255, 255)),
                                    (subtitle, sf, cy + H * 0.11, (235, 228, 220))):
            if not text:
                continue
            # soft shadow then text
            d.text((W / 2 + 3, y + 3), text, font=font, anchor="mm", fill=(0, 0, 0))
            d.text((W / 2, y), text, font=font, anchor="mm", fill=fill)
        return self.still(bg, seconds)


# --------------------------------------------------------------------------- final assembly
def assemble(segs: list[Path], a: argparse.Namespace, out: Path, work: Path) -> float:
    T = a.transition
    durs = [duration_of(s) for s in segs]
    for s, d in zip(segs, durs):
        if d <= T + 0.1:
            raise SystemExit(f"{s.name} is only {d:.2f}s - shorter than the {T}s transition. "
                             f"Lower --transition or --photo-seconds.")
    inputs: list[str] = []
    for s in segs:
        inputs += ["-i", str(s)]

    parts: list[str] = []
    vlast, alast, length = "[0:v]", "[0:a]", durs[0]
    for i in range(1, len(segs)):
        off = length - T
        vo, ao = f"[v{i}]", f"[a{i}]"
        parts.append(f"{vlast}[{i}:v]xfade=transition={a.style}:duration={T}:offset={off:.3f}{vo}")
        parts.append(f"{alast}[{i}:a]acrossfade=d={T}:c1=tri:c2=tri{ao}")
        vlast, alast, length = vo, ao, off + durs[i]

    fade = 1.5
    parts.append(f"{vlast}fade=t=in:st=0:d={fade},fade=t=out:st={length - fade:.3f}:d={fade},format=yuv420p[vout]")
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs]
    if a.music:
        mus = []
        for m in a.music:
            mus += ["-stream_loop", "-1", "-i", m] if len(a.music) == 1 else ["-i", m]
        cmd += mus
        n = len(segs)
        if len(a.music) > 1:
            parts.append("".join(f"[{n + k}:a]" for k in range(len(a.music)))
                         + f"concat=n={len(a.music)}:v=0:a=1,aloop=loop=-1:size=2e9[mraw]")
            mlabel = "[mraw]"
        else:
            mlabel = f"[{n}:a]"
        parts.append(f"{mlabel}aresample=48000,aformat=channel_layouts=stereo,atrim=0:{length:.3f},"
                     f"volume={a.music_volume},afade=t=in:d=2,afade=t=out:st={max(0, length - 4):.3f}:d=4[mus]")
        parts.append(f"{alast}[mus]amix=inputs=2:duration=first:normalize=0,"
                     f"afade=t=out:st={length - fade:.3f}:d={fade}[aout]")
    else:
        parts.append(f"{alast}afade=t=out:st={length - fade:.3f}:d={fade}[aout]")

    script = work / "graph.txt"
    script.write_text(";\n".join(parts))
    cmd += ["-filter_complex_script", str(script), "-map", "[vout]", "-map", "[aout]",
            "-t", f"{length:.3f}",
            "-c:v", "libx264", "-preset", a.preset, "-crf", str(a.crf), "-profile:v", "high",
            "-level", "4.2", "-pix_fmt", "yuv420p", "-r", str(a.fps),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    run(cmd)
    return length


# --------------------------------------------------------------------------- main
def main() -> None:
    p = argparse.ArgumentParser(description="Photos + clips -> one projector-ready movie.")
    p.add_argument("media", nargs="+", help="Folder(s) and/or files of photos and videos")
    p.add_argument("-o", "--output", default="welcome_home.mp4")
    p.add_argument("--title", default="Welcome Home", help='Opening title ("" to skip)')
    p.add_argument("--subtitle", default="", help='e.g. "Baby Aria  ·  9 November 2026"')
    p.add_argument("--ending", default="", help='Optional closing card text, e.g. "Our family of four"')
    p.add_argument("--music", nargs="*", default=[], help="Background music file(s), played in order and looped")
    p.add_argument("--music-volume", type=float, default=0.8)
    p.add_argument("--clip-volume", type=float, default=None,
                   help="Volume of the video clips' own sound (default 1.0, or 0.35 when music is added; 0 = mute)")
    p.add_argument("--order", choices=["date", "name"], default="date",
                   help="date = when it was taken (EXIF/metadata); name = filename order")
    p.add_argument("--photo-seconds", type=float, default=5.0)
    p.add_argument("--title-seconds", type=float, default=6.0)
    p.add_argument("--max-clip-seconds", type=float, default=0,
                   help="Trim long videos to their middle N seconds (0 = keep full clips)")
    p.add_argument("--transition", type=float, default=1.0, help="Cross-fade length in seconds")
    p.add_argument("--style", default="fade",
                   help="xfade style: fade, fadeblack, dissolve, smoothleft, circleopen, ... (default fade)")
    p.add_argument("--fill", choices=["blur", "crop"], default="blur",
                   help="blur = show whole photo on blurred backdrop (best for portrait phone shots); crop = fill screen")
    p.add_argument("--zoom", type=float, default=0.10, help="Ken Burns zoom amount (0.10 = 10%%)")
    p.add_argument("--no-motion", action="store_true", help="Disable Ken Burns motion on photos")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--crf", type=int, default=18, help="Quality (lower = better/bigger). 18 is visually lossless")
    p.add_argument("--preset", default="medium")
    p.add_argument("--font", default=None, help="Path to a .ttf/.otf font for the title cards")
    p.add_argument("--keep-temp", action="store_true")
    a = p.parse_args()

    if a.clip_volume is None:
        a.clip_volume = 0.35 if a.music else 1.0
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found - install ffmpeg first (https://ffmpeg.org/download.html)")

    files: list[Path] = []
    for m in a.media:
        mp = Path(m)
        cands = sorted(mp.rglob("*")) if mp.is_dir() else [mp]
        files += [f for f in cands if f.is_file() and f.suffix.lower() in PHOTO_EXT | VIDEO_EXT
                  and not f.name.startswith(".")]
    if not HEIC_OK and any(f.suffix.lower() in {".heic", ".heif"} for f in files):
        raise SystemExit("HEIC photos found: run `pip install pillow-heif` first.")
    if not files:
        raise SystemExit("No photos or videos found.")
    files.sort(key=(lambda f: (capture_time(f), f.name)) if a.order == "date" else (lambda f: f.name.lower()))

    work = Path(tempfile.mkdtemp(prefix="homecoming_"))
    b = Builder(a, work)
    segs: list[Path] = []
    try:
        first_photo = next((f for f in files if f.suffix.lower() in PHOTO_EXT), None)
        if a.title:
            print("• title card")
            segs.append(b.title_card(a.title, a.subtitle, first_photo, a.title_seconds))
        for i, f in enumerate(files, 1):
            kind = "photo" if f.suffix.lower() in PHOTO_EXT else "video"
            print(f"• [{i}/{len(files)}] {kind}: {f.name}")
            segs.append(b.photo(f) if kind == "photo" else b.video(f))
        if a.ending:
            print("• closing card")
            last_photo = next((f for f in reversed(files) if f.suffix.lower() in PHOTO_EXT), None)
            segs.append(b.title_card(a.ending, "", last_photo, a.title_seconds))
        print(f"• stitching {len(segs)} shots with {a.transition}s '{a.style}' transitions...")
        out = Path(a.output).resolve()
        if len(segs) == 1:
            shutil.copy(segs[0], out)
            length = duration_of(out)
        else:
            length = assemble(segs, a, out, work)
        m, s = divmod(int(round(length)), 60)
        print(f"\n✅ Done: {out}  ({m}m {s:02d}s, {out.stat().st_size / 1e6:.1f} MB)")
        print("   Tip: set your projector/player to 'repeat' for a continuous loop.")
    finally:
        if a.keep_temp:
            print(f"(temp files kept in {work})")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
