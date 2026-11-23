"""Roblox asset files for /roblox-cloth.

Since 2025 Roblox gives files of player-made assets only to signed-in requests. Assets made by Roblox still download
without signing in; with a free Open Cloud API key (permission legacy-asset:manage) every public asset works.
"""
import re
import struct

import httpx

ASSET_DELIVERY = "https://assetdelivery.roblox.com/v1/asset/"
OPEN_CLOUD = "https://apis.roblox.com/asset-delivery-api/v1/assetId/{id}"
ID_RE = re.compile(rb"(?:[?&]id=|rbxassetid://|/asset/\?id=)(\d{3,20})", re.I)


class NeedsKey(Exception):
    """Roblox wants a signed-in request for this asset."""


def lz4_block(data: bytes, size: int) -> bytes:
    """Decompresses one LZ4 block (the format inside binary Roblox files)."""
    out = bytearray()
    i = 0
    while i < len(data):
        token = data[i]
        i += 1
        literal = token >> 4
        if literal == 15:
            while True:
                b = data[i]
                i += 1
                literal += b
                if b != 255:
                    break
        out += data[i:i + literal]
        i += literal
        if i >= len(data) or len(out) >= size:
            break
        offset = data[i] | data[i + 1] << 8
        i += 2
        length = token & 15
        if length == 15:
            while True:
                b = data[i]
                i += 1
                length += b
                if b != 255:
                    break
        length += 4
        start = len(out) - offset
        for k in range(length):
            out.append(out[start + k])
    return bytes(out)


def readable_parts(blob: bytes) -> list[bytes]:
    """The parts of a Roblox file where asset links can be found (binary files are split into chunks, often LZ4)."""
    if not blob.startswith(b"<roblox!"):
        return [blob]
    parts, pos = [], 32
    while pos + 16 <= len(blob):
        name, comp, raw = blob[pos:pos + 4], *struct.unpack("<II", blob[pos + 4:pos + 12])
        pos += 16
        size = comp or raw
        data = blob[pos:pos + size]
        pos += size
        if comp == 0:
            parts.append(data)
        elif data[:4] != b"\x28\xb5\x2f\xfd":  # zstd chunks cannot be read without an extra package
            try:
                parts.append(lz4_block(data, raw))
            except IndexError:
                pass
        if name == b"END\x00":
            break
    return parts


def linked_asset_id(blob: bytes) -> str | None:
    """The first asset linked inside a clothing / decal file (its template or texture)."""
    for part in readable_parts(blob):
        m = ID_RE.search(part)
        if m:
            return m.group(1).decode()
    return None


async def download(client: httpx.AsyncClient, asset_id: str, api_key: str | None) -> bytes:
    """The file of an asset. Raises NeedsKey when Roblox wants a signed-in request and no (working) key is set."""
    r = await client.get(ASSET_DELIVERY, params={"id": asset_id})
    if r.status_code == 200:
        return r.content
    if r.status_code not in (401, 403):
        raise httpx.HTTPError(f"HTTP {r.status_code}")
    if not api_key:
        raise NeedsKey()
    r = await client.get(OPEN_CLOUD.format(id=asset_id), headers={"x-api-key": api_key})
    if r.status_code in (401, 403):
        raise NeedsKey()
    if r.status_code != 200:
        raise httpx.HTTPError(f"HTTP {r.status_code}")
    location = r.json().get("location")
    if not location:
        raise httpx.HTTPError("no location")
    f = await client.get(location)
    if f.status_code != 200:
        raise httpx.HTTPError(f"HTTP {f.status_code}")
    return f.content


async def thumbnail(client: httpx.AsyncClient, asset_id: str) -> str | None:
    r = await client.get("https://thumbnails.roblox.com/v1/assets",
                         params={"assetIds": asset_id, "size": "420x420", "format": "Png"})
    try:
        item = (r.json().get("data") or [{}])[0]
    except ValueError:
        return None
    return item.get("imageUrl") if item.get("state") == "Completed" else None
