"""Builds the app icon files from ui/logo.svg (run only after changing the logo; the results are in git).

Draws the SVG at every Windows icon size inside a hidden WebView2 window (pywebview, already installed), then writes:
  ui/logo.ico  - multi-size icon for the window, taskbar and shortcuts
  ui/logo.png  - 256x256 picture for the README and the app page
No extra packages are needed.
"""
import base64
import json
import struct
import sys
import time
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "ui" / "logo.svg"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

DRAW_JS = """
(async () => {
  const img = new Image();
  img.src = 'data:image/svg+xml;base64,' + %s;
  await img.decode();
  const out = {};
  for (const size of %s) {
    const c = document.createElement('canvas');
    c.width = c.height = size;
    const g = c.getContext('2d');
    g.imageSmoothingQuality = 'high';
    g.drawImage(img, 0, 0, size, size);
    out[size] = c.toDataURL('image/png').split(',')[1];
  }
  window.__icons = JSON.stringify(out);
})()
"""


def render_all() -> dict[int, bytes]:
    result: dict[int, bytes] = {}
    svg_b64 = base64.b64encode(SVG.read_bytes()).decode()
    window = webview.create_window("icon", html="<html><body></body></html>", hidden=True)

    def work():
        try:
            window.events.loaded.wait(15)
            # evaluate_js does not wait for promises here, so start the drawing and then ask for the result
            window.evaluate_js(DRAW_JS % (json.dumps(svg_b64), json.dumps(list(SIZES))))
            data = None
            for _ in range(100):
                raw = window.evaluate_js("window.__icons || null")
                if raw:
                    data = json.loads(raw)
                    break
                time.sleep(0.1)
            if not data:
                print("the hidden window did not draw the icon")
                return
            for size, b64 in data.items():
                result[int(size)] = base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            print('renderer error:', repr(exc)[:500])
        finally:
            window.destroy()

    webview.start(work)
    for size in SIZES:
        png = result.get(size)
        if not png or struct.unpack(">II", png[16:24]) != (size, size):
            raise SystemExit(f"drawing the {size}x{size} icon failed")
    return result


def write_ico(images: dict[int, bytes], target: Path) -> None:
    """An .ico file may hold PNG images directly (Windows Vista and later)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in sorted(images.items()):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    target.write_bytes(header + entries + blobs)


def main() -> int:
    images = render_all()
    write_ico(images, ROOT / "ui" / "logo.ico")
    (ROOT / "ui" / "logo.png").write_bytes(images[256])
    print(f"ui/logo.ico ({len(images)} sizes) and ui/logo.png written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
