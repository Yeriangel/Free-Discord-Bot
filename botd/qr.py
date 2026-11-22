"""A small QR code maker (byte mode, error correction level M, versions 1 to 10: up to 213 bytes) with a PNG writer.

No extra packages: the encoding follows the QR standard (ISO/IEC 18004) step by step.
"""
import struct
import zlib

# error correction level M, versions 1..10: (ECC codewords per block, number of blocks)
ECC_M = {1: (10, 1), 2: (16, 1), 3: (26, 1), 4: (18, 2), 5: (24, 2), 6: (16, 4), 7: (18, 4), 8: (22, 4), 9: (22, 5), 10: (26, 5)}
MAX_VERSION = 10
FORMAT_BITS_M = 0


def _raw_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        align = ver // 7 + 2
        result -= (25 * align - 10) * align - 55
        if ver >= 7:
            result -= 36
    return result


def _data_capacity(ver: int) -> int:
    ecc, blocks = ECC_M[ver]
    return _raw_modules(ver) // 8 - ecc * blocks


def capacity_bytes(ver: int = MAX_VERSION) -> int:
    count_bits = 8 if ver < 10 else 16
    return (_data_capacity(ver) * 8 - 4 - count_bits) // 8


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _alignment_positions(ver: int, size: int) -> list[int]:
    if ver == 1:
        return []
    count = ver // 7 + 2
    step = (ver * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    return sorted([size - 7 - i * step for i in range(count - 1)] + [6])


class _Matrix:
    def __init__(self, ver: int):
        self.ver = ver
        self.size = ver * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def set_fn(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.function[y][x] = True

    def draw_function_patterns(self) -> None:
        size = self.size
        for i in range(size):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set_fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.ver, size)
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)  # reserve the area; drawn again with the real mask later
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self.set_fn(a, b, bit)
                self.set_fn(b, a, bit)

    def draw_format(self, mask: int) -> None:
        data = FORMAT_BITS_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        def bit(i):
            return (bits >> i) & 1 == 1

        size = self.size
        for i in range(6):
            self.set_fn(8, i, bit(i))
        self.set_fn(8, 7, bit(6))
        self.set_fn(8, 8, bit(7))
        self.set_fn(7, 8, bit(8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, bit(i))
        for i in range(8):
            self.set_fn(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_fn(8, size - 15 + i, bit(i))
        self.set_fn(8, size - 8, True)

    def draw_codewords(self, data: list[int]) -> None:
        size, i, total = self.size, 0, len(data) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = size - 1 - vert if upward else vert
                    if not self.function[y][x] and i < total:
                        self.dark[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        tests = (
            lambda x, y: (x + y) % 2 == 0,
            lambda x, y: y % 2 == 0,
            lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0,
            lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0,
            lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
        )
        test = tests[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.function[y][x] and test(x, y):
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self) -> int:
        """Standard penalty rules 1, 2 and 4 (enough to choose a mask that scans well)."""
        size, dark, score = self.size, self.dark, 0
        for lines in (dark, [list(col) for col in zip(*dark)]):
            for row in lines:
                run, color = 0, None
                for cell in row:
                    if cell == color:
                        run += 1
                        if run == 5:
                            score += 3
                        elif run > 5:
                            score += 1
                    else:
                        color, run = cell, 1
        for y in range(size - 1):
            for x in range(size - 1):
                if dark[y][x] == dark[y][x + 1] == dark[y + 1][x] == dark[y + 1][x + 1]:
                    score += 3
        total = sum(row.count(True) for row in dark)
        k = (abs(total * 20 - size * size * 10) + size * size - 1) // (size * size) - 1
        return score + max(0, k) * 10


def encode(text: str) -> list[list[bool]]:
    """Text -> QR matrix (True = dark). Raises ValueError when the text is too long."""
    data = text.encode("utf-8")
    ver = next((v for v in range(1, MAX_VERSION + 1) if len(data) <= capacity_bytes(v)), None)
    if ver is None:
        raise ValueError(f"too long: at most {capacity_bytes()} bytes")
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    put(0b0100, 4)
    put(len(data), 8 if ver < 10 else 16)
    for b in data:
        put(b, 8)
    capacity = _data_capacity(ver) * 8
    put(0, min(4, capacity - len(bits)))
    put(0, (-len(bits)) % 8)
    codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(codewords) < _data_capacity(ver):
        codewords.append(pad)
        pad ^= 0xEC ^ 0x11

    ecc_len, num_blocks = ECC_M[ver]
    raw = _raw_modules(ver) // 8
    short_blocks = num_blocks - raw % num_blocks
    short_len = raw // num_blocks
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(num_blocks):
        length = short_len - ecc_len + (0 if i < short_blocks else 1)
        dat = codewords[k:k + length]
        k += length
        ecc = _rs_remainder(dat, divisor)
        if i < short_blocks:
            dat = dat + [0]
        blocks.append(dat + ecc)
    final = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_blocks:
                final.append(block[i])

    base = _Matrix(ver)
    base.draw_function_patterns()
    base.draw_codewords(final)
    best, best_score = None, None
    for mask in range(8):
        m = _Matrix(ver)
        m.dark = [row[:] for row in base.dark]
        m.function = base.function
        m.apply_mask(mask)
        m.draw_format(mask)
        score = m.penalty()
        if best_score is None or score < best_score:
            best, best_score = m, score
    return best.dark


def to_png(matrix: list[list[bool]], scale: int = 10, border: int = 4) -> bytes:
    """A black-on-white PNG of the matrix."""
    n = len(matrix)
    width = (n + border * 2) * scale
    rows = []
    for y in range(-border, n + border):
        line = bytearray()
        for x in range(-border, n + border):
            dark = 0 <= x < n and 0 <= y < n and matrix[y][x]
            line += (b"\x00" if dark else b"\xff") * scale
        rows.extend([b"\x00" + bytes(line)] * scale)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    header = struct.pack(">IIBBBBB", width, width, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + \
        chunk(b"IEND", b"")
