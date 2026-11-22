"""Image work for the tool commands (/opacity, /file-format), done with ffmpeg plus two small writers of our own:
PSD and PDF (ffmpeg cannot write those). No extra Python packages."""
import asyncio
import struct
import subprocess
import zlib
from pathlib import Path

# target format -> file extension
FORMATS = {"png": "png", "jpeg": "jpg", "webp": "webp", "gif": "gif", "tiff": "tiff", "bmp": "bmp", "psd": "psd",
           "pdf": "pdf"}
READABLE = (".png", ".jpg", ".jpeg", ".jfif", ".webp", ".gif", ".tif", ".tiff", ".bmp", ".psd", ".ico", ".tga", ".avif",
            ".heic", ".jxl", ".qoi", ".pcx", ".ppm", ".pgm", ".sgi", ".dds", ".exr", ".hdr")
MAX_PIXELS = 40_000_000
GIF_FILTER = "split[a][b];[a]palettegen=reserve_transparent=1[p];[b][p]paletteuse=alpha_threshold=128"


class ImageError(Exception):
    """Something the user should hear about (unreadable file, too big, ...)."""


async def run_ffmpeg(ffmpeg: str, *args: str, timeout: float = 120) -> None:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("timeout")
    if proc.returncode != 0:
        raise RuntimeError((err or b"").decode(errors="replace")[-300:])


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ImageError("not a png")
    return struct.unpack(">II", head[16:24])


async def to_rgba_png(ffmpeg: str, src: Path, dst: Path) -> tuple[int, int]:
    """Any image ffmpeg can read -> PNG with transparency (first frame of an animation). Returns (width, height)."""
    try:
        await run_ffmpeg(ffmpeg, "-i", str(src), "-frames:v", "1", "-pix_fmt", "rgba", str(dst))
    except RuntimeError:
        raise ImageError("unreadable") from None
    w, h = png_size(dst)
    if w * h > MAX_PIXELS:
        raise ImageError("too big")
    return w, h


async def raw_rgba(ffmpeg: str, png: Path, out: Path) -> bytes:
    await run_ffmpeg(ffmpeg, "-i", str(png), "-f", "rawvideo", "-pix_fmt", "rgba", str(out))
    return out.read_bytes()


def write_psd(rgba: bytes, width: int, height: int) -> bytes:
    """A Photoshop file with one layer ("Layer 1") that keeps the transparency, plus the flat image (uncompressed)."""
    n = width * height
    red, green, blue, alpha = (rgba[c::4] for c in range(4))
    assert len(red) == n
    header = b"8BPS" + struct.pack(">H6xHIIHH", 1, 4, height, width, 8, 3)
    # layer record: bounds, 4 channels (-1 = transparency, 0/1/2 = red/green/blue), normal blending, full opacity
    channels = [(-1, alpha), (0, red), (1, green), (2, blue)]
    name = b"Layer 1"
    pascal = bytes([len(name)]) + name
    pascal += b"\x00" * (-len(pascal) % 4)
    extra = struct.pack(">I", 0) + struct.pack(">I", 0) + pascal
    record = struct.pack(">iiiiH", 0, 0, height, width, len(channels))
    record += b"".join(struct.pack(">hI", cid, 2 + n) for cid, _ in channels)
    record += b"8BIMnorm" + bytes([255, 0, 0, 0]) + struct.pack(">I", len(extra)) + extra
    image_data = b"".join(struct.pack(">H", 0) + data for _, data in channels)
    # a negative layer count says the first extra channel of the flat image is its transparency
    layer_info = struct.pack(">h", -1) + record + image_data
    layer_info += b"\x00" * (len(layer_info) % 2)
    layer_section = struct.pack(">I", len(layer_info)) + layer_info + struct.pack(">I", 0)
    merged = struct.pack(">H", 0) + red + green + blue + alpha
    return (header + struct.pack(">I", 0) + struct.pack(">I", 0) + struct.pack(">I", len(layer_section)) + layer_section
            + merged)


def write_pdf(rgba: bytes, width: int, height: int) -> bytes:
    """A one-page PDF that shows the picture at its size (1 pixel = 1 point), transparency kept."""
    rgb = b"".join(rgba[i:i + 3] for i in range(0, len(rgba), 4))
    alpha = rgba[3::4]
    has_alpha = any(a != 255 for a in alpha)
    content = f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources << /XObject << /Im0 5 0 R >> >> "
        f"/Contents 4 0 R >>".encode(),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    image = zlib.compress(rgb, 6)
    smask = f" /SMask 6 0 R" if has_alpha else ""
    objects.append(f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB "
                   f"/BitsPerComponent 8 /Filter /FlateDecode{smask} /Length {len(image)} >>\nstream\n".encode()
                   + image + b"\nendstream")
    if has_alpha:
        mask = zlib.compress(alpha, 6)
        objects.append(f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceGray "
                       f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(mask)} >>\nstream\n".encode()
                       + mask + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


async def change_opacity(ffmpeg: str, src: Path, dst: Path, percent: int) -> None:
    """Makes the whole picture more see-through: 100 = unchanged, 1 = almost invisible. Result is a PNG."""
    try:
        await run_ffmpeg(ffmpeg, "-i", str(src), "-frames:v", "1",
                         "-vf", f"format=rgba,colorchannelmixer=aa={max(1, min(percent, 100)) / 100:.2f}", str(dst))
    except RuntimeError:
        raise ImageError("unreadable") from None
    w, h = png_size(dst)
    if w * h > MAX_PIXELS:
        raise ImageError("too big")


async def convert(ffmpeg: str, src: Path, target: str, workdir: Path) -> Path:
    """Converts an image to the target format (a key of FORMATS) and returns the new file."""
    ext = FORMATS[target]
    dst = workdir / f"out.{ext}"
    animated_source = src.suffix.lower() in (".gif", ".webp")
    if target == "gif" and animated_source:
        try:  # keep the animation
            await run_ffmpeg(ffmpeg, "-i", str(src), "-filter_complex", GIF_FILTER, "-loop", "0", str(dst))
            return dst
        except RuntimeError:
            pass
    if target == "webp" and src.suffix.lower() == ".gif":
        try:  # animated GIF -> animated WEBP
            await run_ffmpeg(ffmpeg, "-i", str(src), "-c:v", "libwebp_anim", "-loop", "0", "-q:v", "80", str(dst))
            return dst
        except RuntimeError:
            pass
    png = workdir / "norm.png"
    w, h = await to_rgba_png(ffmpeg, src, png)
    if target == "png":
        return png
    if target in ("jpeg", "bmp"):  # no transparency in these formats: put the picture on white
        flat = (f"color=c=white:s={w}x{h}[bg];[bg][0:v]overlay=shortest=1:format=auto,"
                + ("format=yuvj444p" if target == "jpeg" else "format=bgr24"))
        extra = ["-q:v", "2"] if target == "jpeg" else []
        await run_ffmpeg(ffmpeg, "-i", str(png), "-filter_complex", flat, "-frames:v", "1", *extra, str(dst))
    elif target == "webp":
        await run_ffmpeg(ffmpeg, "-i", str(png), "-c:v", "libwebp", "-q:v", "90", str(dst))
    elif target == "gif":
        await run_ffmpeg(ffmpeg, "-i", str(png), "-filter_complex", GIF_FILTER, "-frames:v", "1", str(dst))
    elif target == "tiff":
        await run_ffmpeg(ffmpeg, "-i", str(png), "-c:v", "tiff", "-compression_algo", "deflate", str(dst))
    else:  # psd / pdf: written here from the raw pixels
        rgba = await raw_rgba(ffmpeg, png, workdir / "raw.rgba")
        dst.write_bytes(write_psd(rgba, w, h) if target == "psd" else write_pdf(rgba, w, h))
    return dst
