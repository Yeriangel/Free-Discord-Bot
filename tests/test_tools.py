"""Tests for the info & tool commands: Morse, binary, QR codes (read back and checked), QR payloads, and video -> GIF."""
import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from botd import qr  # noqa: E402
from botd.modules import tools  # noqa: E402


def test_morse_and_binary():
    code, skipped = tools.to_morse("SOS help 123!")
    assert code == "... --- ... / .... . .-.. .--. / .---- ..--- ...-- -.-.--" and skipped == []
    assert tools.from_morse(code) == "SOS HELP 123!"
    assert tools.is_morse(code) and not tools.is_morse("hello")
    code, skipped = tools.to_morse("hi שלום")
    assert code == ".... .." and set(skipped) == set("שלום")
    b = tools.to_binary("Hi")
    assert b == "01001000 01101001" and tools.is_binary(b) and tools.from_binary(b) == "Hi"
    assert tools.from_binary(tools.to_binary("שלום")) == "שלום"
    assert not tools.is_binary("0101")  # not whole bytes: treat as text


def test_qr_payloads():
    assert tools.qr_payload("url", "example.com") == "https://example.com"
    assert tools.qr_payload("url", "http://a.b") == "http://a.b"
    assert tools.qr_payload("wifi", "My;Net", "p@ss:1") == r"WIFI:T:WPA;S:My\;Net;P:p@ss\:1;;"
    assert tools.qr_payload("wifi", "Open", "x", "nopass") == "WIFI:T:nopass;S:Open;;"
    assert tools.qr_payload("email", "a@b.co", "Hi") == "mailto:a@b.co?subject=Hi"
    assert tools.qr_payload("phone", "+972 50 123") == "tel:+97250123"
    assert tools.qr_payload("sms", "050", "hey") == "SMSTO:050:hey"
    assert tools.qr_payload("text", "  hello ") == "hello"


# ---- a small QR reader, used to check that what the encoder draws can be read back ----
def _read_qr(matrix):
    size = len(matrix)
    ver = (size - 17) // 4
    bit = lambda x, y: matrix[y][x]  # noqa: E731
    # format information, first copy (around the top-left finder) and second copy
    first = [bit(8, i) for i in range(6)] + [bit(8, 7), bit(8, 8), bit(7, 8)] + [bit(14 - i, 8) for i in range(9, 15)]
    second = [bit(size - 1 - i, 8) for i in range(8)] + [bit(8, size - 15 + i) for i in range(8, 15)]
    value = sum(1 << i for i, b in enumerate(first) if b)
    assert value == sum(1 << i for i, b in enumerate(second) if b), "the two format copies differ"
    found = None
    for data in range(32):
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        if (data << 10 | rem) ^ 0x5412 == value:
            found = data
    assert found is not None, "format bits are not a valid BCH code"
    level, mask = found >> 3, found & 7
    assert level == 0  # M
    assert matrix[size - 8][8], "dark module missing"
    # timing patterns
    assert all(matrix[6][i] == (i % 2 == 0) for i in range(8, size - 8))
    assert all(matrix[i][6] == (i % 2 == 0) for i in range(8, size - 8))
    # function map from a fresh matrix of the same version
    m = qr._Matrix(ver)
    m.draw_function_patterns()
    masks = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]
    bits = []
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not m.function[y][x]:
                    bits.append(matrix[y][x] ^ masks[mask](x, y))
        right -= 2
    raw = [int("".join("1" if b else "0" for b in bits[i:i + 8]), 2) for i in range(0, len(bits) // 8 * 8, 8)]
    ecc_len, nblocks = qr.ECC_M[ver]
    total = qr._raw_modules(ver) // 8
    raw = raw[:total]
    short = nblocks - total % nblocks
    short_len = total // nblocks
    lengths = [short_len - ecc_len + (0 if i < short else 1) for i in range(nblocks)]
    data_blocks = [[] for _ in range(nblocks)]
    k = 0
    for i in range(max(lengths)):
        for j in range(nblocks):
            if i < lengths[j]:
                data_blocks[j].append(raw[k])
                k += 1
    ecc_blocks = [[] for _ in range(nblocks)]
    for i in range(ecc_len):
        for j in range(nblocks):
            ecc_blocks[j].append(raw[k])
            k += 1
    divisor = qr._rs_divisor(ecc_len)
    for d, e in zip(data_blocks, ecc_blocks):
        assert qr._rs_remainder(d, divisor) == e, "error correction does not match"
    data = [b for blk in data_blocks for b in blk]
    stream = "".join(f"{b:08b}" for b in data)
    assert stream[:4] == "0100"  # byte mode
    count_len = 8 if ver < 10 else 16
    n = int(stream[4:4 + count_len], 2)
    start = 4 + count_len
    return bytes(int(stream[start + 8 * i:start + 8 * i + 8], 2) for i in range(n)).decode("utf-8")


def test_qr_codes_read_back():
    samples = ["https://example.com", "Hello, world!", "שלום עולם 👋", "WIFI:T:WPA;S:Home;P:secret;;",
               "https://www.roblox.com/users/1/profile?x=" + "y" * 100, "x" * qr.capacity_bytes()]
    for text in samples:
        matrix = qr.encode(text)
        assert _read_qr(matrix) == text, text
    sizes = {len(qr.encode("a" * n)) for n in (1, 20, 60, 150, 213)}
    assert len(sizes) == 5  # different versions were used
    try:
        qr.encode("x" * (qr.capacity_bytes() + 1))
        raise AssertionError("too long text should fail")
    except ValueError:
        pass
    png = qr.to_png(qr.encode("hi"), scale=4)
    assert png.startswith(b"\x89PNG") and int.from_bytes(png[16:20], "big") == (21 + 8) * 4


def test_video_to_gif():
    ffmpeg = tools.find_ffmpeg()
    if not ffmpeg:
        print("ffmpeg not installed: skipping the GIF test")
        return
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.mp4", Path(tmp) / "out.gif"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=duration=3:size=640x360:rate=30", "-pix_fmt", "yuv420p", str(src)],
                       check=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        asyncio.run(tools.video_to_gif(ffmpeg, src, dst, 320, 10, 2))
        data = dst.read_bytes()
        assert data[:6] in (b"GIF89a", b"GIF87a")
        assert int.from_bytes(data[6:8], "little") == 320
        assert len(data) < tools.MAX_GIF_BYTES


SIGNATURES = {"png": [b"\x89PNG"], "jpeg": [b"\xff\xd8\xff"], "gif": [b"GIF89a", b"GIF87a"], "tiff": [b"II*\x00", b"MM\x00*"],
              "bmp": [b"BM"], "psd": [b"8BPS"], "pdf": [b"%PDF-"]}


def _make_test_image(ffmpeg, path, size="64x48", animated=False):
    """A half-transparent test picture (or a short animation)."""
    source = "testsrc=duration=1:size=%s:rate=5" % size if animated else "color=c=red:size=%s:duration=1" % size
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", source]
    if animated:
        args += ["-filter_complex", "split[a][b];[a]palettegen[p];[b][p]paletteuse", "-loop", "0"]
    else:
        args += ["-frames:v", "1", "-vf", "format=rgba,colorchannelmixer=aa=0.5"]
    subprocess.run(args + [str(path)], check=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def test_image_tools():
    from botd import images

    ffmpeg = tools.find_ffmpeg()
    if not ffmpeg:
        print("ffmpeg not installed: skipping the image tests")
        return

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "in.png"
            _make_test_image(ffmpeg, src)
            # every target format, with the right file signature
            for target in images.FORMATS:
                work = tmp / target
                work.mkdir()
                out = await images.convert(ffmpeg, src, target, work)
                data = out.read_bytes()
                if target == "webp":
                    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", target
                else:
                    assert any(data.startswith(sig) for sig in SIGNATURES[target]), (target, data[:8])
                # what we wrote can be read back (ffmpeg reads PSD, WEBP, TIFF ... but not PDF)
                if target != "pdf":
                    back = work / "back.png"
                    assert await images.to_rgba_png(ffmpeg, out, back) == (64, 48), target
            # PSD keeps the transparency: read it back and look at the alpha channel
            work = tmp / "psd2"
            work.mkdir()
            back = work / "back.png"
            await images.to_rgba_png(ffmpeg, await images.convert(ffmpeg, src, "psd", work), back)
            raw = await images.raw_rgba(ffmpeg, back, work / "raw")
            assert abs(raw[3] - 128) <= 1, raw[:4]
            # PDF: one page with the picture and its transparency mask
            pdf = images.write_pdf(bytes([255, 0, 0, 128]) * 6, 3, 2)
            assert b"/MediaBox [0 0 3 2]" in pdf and b"/SMask 6 0 R" in pdf and pdf.rstrip().endswith(b"%%EOF")
            assert b"/SMask" not in images.write_pdf(bytes([1, 2, 3, 255]) * 4, 2, 2)
            # opacity: 25% of a fully solid picture -> alpha about 64
            solid = tmp / "solid.png"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            "color=c=blue:size=10x10:duration=1", "-frames:v", "1", str(solid)], check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out = tmp / "op.png"
            await images.change_opacity(ffmpeg, solid, out, 25)
            raw = await images.raw_rgba(ffmpeg, out, tmp / "op.raw")
            assert len(raw) == 400 and all(60 <= a <= 68 for a in raw[3::4]), raw[3]
            # an animated GIF stays animated as GIF and becomes an animated WEBP
            anim = tmp / "anim.gif"
            _make_test_image(ffmpeg, anim, animated=True)
            work = tmp / "anim"
            work.mkdir()
            out = await images.convert(ffmpeg, anim, "webp", work)
            assert b"ANIM" in out.read_bytes()[:200]
            # unreadable input
            bad = tmp / "bad.png"
            bad.write_bytes(b"not an image")
            try:
                await images.convert(ffmpeg, bad, "jpeg", tmp)
                raise AssertionError("should fail")
            except images.ImageError:
                pass

    asyncio.run(run())


def test_seed_map_link():
    url = tools.seed_map_url("12345")
    assert url.startswith("https://www.chunkbase.com/apps/seed-map#seed=12345&platform=java_26_3&dimension=overworld")
    assert "seed=hello%20world" in tools.seed_map_url("hello world", "bedrock_26_60", "nether", 100, -50)
    assert "platform=bedrock_26_60&dimension=nether" in tools.seed_map_url("1", "bedrock_26_60", "nether")
    assert "x=100&z=-50" in tools.seed_map_url("1", x=100, z=-50)
    assert "seed=-42" in tools.seed_map_url("-42")
    assert len(tools.SEED_PLATFORMS) <= 25  # Discord allows 25 choices


def test_time_zones_and_parsing():
    import datetime as dt

    from botd import wintz

    D = dt.datetime
    if wintz.zones():  # Windows' own time zone rules, with summer time
        assert wintz.utc_offset_minutes("Israel Standard Time", D(2026, 7, 1, 12)) == 180
        assert wintz.utc_offset_minutes("Israel Standard Time", D(2026, 12, 24, 21)) == 120
        assert wintz.utc_offset_minutes("Eastern Standard Time", D(2026, 3, 9, 12)) == -240
        assert wintz.utc_offset_minutes("AUS Eastern Standard Time", D(2026, 1, 15, 12)) == 660  # southern summer
        assert wintz.utc_offset_minutes("India Standard Time", D(2026, 6, 1)) == 330
        assert wintz.search("jerusalem")[0][1] == "Israel Standard Time"
        assert tools.event_time("21:00", "Israel Standard Time", "24/12/2026")[0] == 1798138800  # 19:00 UTC
        assert tools.event_time("9pm", "jerusalem", "1/7/2027")[0] == 1814464800  # summer: 18:00 UTC
        assert "Israel (UTC+02:00)" == tools.event_time("21:00", "Israel Standard Time", "24/12/2026")[1]
    # fixed offsets work everywhere
    assert wintz.parse_offset("GMT+3") == 180 and wintz.parse_offset("utc-5:30") == -330 and wintz.parse_offset("+15") is None
    assert tools.event_time("10:00", "GMT+3", "1/1/2027")[:2] == (1798786800, "UTC+03:00")  # 07:00 UTC
    assert tools.event_time("25:00", "GMT+3") == "time"
    assert tools.event_time("10:00", "nowhere-zone") == "zone"
    assert tools.event_time("10:00", "GMT+3", "31/02") == "date"
    assert tools.parse_clock("2130") == (21, 30) and tools.parse_clock("12am") == (0, 0) and tools.parse_clock("12:15 PM") == (12, 15)
    today = dt.date(2026, 11, 23)
    assert tools.parse_day("tomorrow", today) == dt.date(2026, 11, 24)
    assert tools.parse_day("24/12", today) == dt.date(2026, 12, 24)
    assert tools.parse_day("1/1", today) == dt.date(2027, 1, 1)  # already passed this year -> next year
    assert tools.parse_day("2027-03-05", today) == dt.date(2027, 3, 5)
    # no date: a time that already passed today means tomorrow
    unix = tools.event_time("00:00", "UTC+00:00")[0]
    assert 0 < unix - dt.datetime.now(dt.timezone.utc).timestamp() <= 86400


def test_roblox_template_id():
    xml = '<Content name="ShirtTemplate"><url>http://www.roblox.com/asset/?id=607785311</url></Content>'
    assert tools.template_id(xml) == "607785311"
    assert tools.template_id('<url>rbxassetid://123456789</url>') == "123456789"
    assert tools.template_id("<roblox></roblox>") is None


def test_commands_install_everywhere():
    names = {c.name: c for c in tools.Tools.__cog_app_commands__}
    assert set(names) == {"roblox-user", "pokedex", "morse", "binary", "qr", "gif", "opacity", "file-format", "seed", "pfp", "time", "roblox-cloth"}
    for c in names.values():
        assert c.allowed_installs.guild and c.allowed_installs.user, c.name
        assert c.allowed_contexts.guild and c.allowed_contexts.dm_channel and c.allowed_contexts.private_channel, c.name


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
