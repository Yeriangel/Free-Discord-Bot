"""Time zones (with summer time) from Windows' own time zone list, for /time.

Python on Windows has no time zone database unless the tzdata package is installed, but Windows keeps the rules
of every zone (including the year-by-year "Dynamic DST" changes) in the registry. Plain offsets like "GMT+3" or
"UTC-5:30" work everywhere.
"""
import datetime as dt
import re
import struct
from functools import lru_cache

KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Time Zones"

# English city names -> Windows zone, so searching works whatever the language of Windows is
CITIES = {
    "jerusalem": "Israel Standard Time", "tel aviv": "Israel Standard Time", "israel": "Israel Standard Time",
    "london": "GMT Standard Time", "dublin": "GMT Standard Time", "lisbon": "GMT Standard Time",
    "paris": "Romance Standard Time", "madrid": "Romance Standard Time", "brussels": "Romance Standard Time",
    "berlin": "W. Europe Standard Time", "rome": "W. Europe Standard Time", "amsterdam": "W. Europe Standard Time",
    "vienna": "W. Europe Standard Time", "stockholm": "W. Europe Standard Time", "warsaw": "Central European Standard Time",
    "athens": "GTB Standard Time", "bucharest": "GTB Standard Time", "kyiv": "FLE Standard Time", "kiev": "FLE Standard Time",
    "helsinki": "FLE Standard Time", "istanbul": "Turkey Standard Time", "moscow": "Russian Standard Time",
    "cairo": "Egypt Standard Time", "dubai": "Arabian Standard Time", "riyadh": "Arab Standard Time",
    "tehran": "Iran Standard Time", "karachi": "Pakistan Standard Time", "delhi": "India Standard Time",
    "mumbai": "India Standard Time", "india": "India Standard Time", "bangkok": "SE Asia Standard Time",
    "singapore": "Singapore Standard Time", "beijing": "China Standard Time", "shanghai": "China Standard Time",
    "hong kong": "China Standard Time", "tokyo": "Tokyo Standard Time", "japan": "Tokyo Standard Time",
    "seoul": "Korea Standard Time", "sydney": "AUS Eastern Standard Time", "melbourne": "AUS Eastern Standard Time",
    "auckland": "New Zealand Standard Time", "new york": "Eastern Standard Time", "toronto": "Eastern Standard Time",
    "miami": "Eastern Standard Time", "chicago": "Central Standard Time", "dallas": "Central Standard Time",
    "mexico city": "Central Standard Time (Mexico)", "denver": "Mountain Standard Time",
    "phoenix": "US Mountain Standard Time", "los angeles": "Pacific Standard Time",
    "san francisco": "Pacific Standard Time", "vancouver": "Pacific Standard Time", "seattle": "Pacific Standard Time",
    "anchorage": "Alaskan Standard Time", "honolulu": "Hawaiian Standard Time", "sao paulo": "E. South America Standard Time",
    "buenos aires": "Argentina Standard Time", "bogota": "SA Pacific Standard Time", "johannesburg": "South Africa Standard Time",
    "lagos": "W. Central Africa Standard Time", "nairobi": "E. Africa Standard Time", "utc": "UTC", "gmt": "UTC",
}
OFFSET_RE = re.compile(r"^(?:gmt|utc)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", re.I)


def _unpack(tzi: bytes):
    bias, std_bias, dst_bias = struct.unpack("<lll", tzi[:12])
    std = struct.unpack("<8H", tzi[12:28])
    dst = struct.unpack("<8H", tzi[28:44])
    return bias, std_bias, dst_bias, std, dst


@lru_cache(maxsize=1)
def _english_names() -> dict[str, str]:
    """Windows zone key -> its English display name ('(UTC+02:00) Jerusalem'), read from Windows' English
    resources (tzres.dll.mui). Empty when Windows has no English resources."""
    import ctypes
    import os
    from ctypes import wintypes

    try:
        import winreg
    except ImportError:
        return {}
    path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "en-US", "tzres.dll.mui")
    if not os.path.exists(path):
        return {}
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.LoadLibraryExW.restype = wintypes.HMODULE
    kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    user32.LoadStringW.argtypes = [wintypes.HMODULE, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
    kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]
    handle = kernel32.LoadLibraryExW(path, None, 0x00000002 | 0x00000020)  # as data file / image resource
    if not handle:
        return {}
    out = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, KEY)
        for i in range(winreg.QueryInfoKey(root)[0]):
            name = winreg.EnumKey(root, i)
            try:
                mui = winreg.QueryValueEx(winreg.OpenKey(root, name), "MUI_Display")[0]
                buf = ctypes.create_unicode_buffer(256)
                if user32.LoadStringW(handle, int(mui.rsplit(",-", 1)[1]), buf, 256):
                    out[name] = buf.value
            except (OSError, ValueError, IndexError):
                continue
    finally:
        kernel32.FreeLibrary(handle)
    return out


def english_city(zone: str) -> str:
    """The English place names of a zone ('Jerusalem', 'Eastern Time (US & Canada)'), or '' if unknown."""
    display = _english_names().get(zone, "")
    if display:
        return re.sub(r"^\(UTC[^)]*\)\s*", "", display)
    cities = [c.title() for c, z in CITIES.items() if z == zone and c not in ("utc", "gmt")]
    return ", ".join(cities[:3])


@lru_cache(maxsize=1)
def zones() -> dict[str, dict]:
    """Windows zone key -> {display, tzi, dynamic: {year: tzi}}. Empty when not on Windows."""
    try:
        import winreg
    except ImportError:
        return {}
    out = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, KEY)
    except OSError:
        return {}
    for i in range(winreg.QueryInfoKey(root)[0]):
        name = winreg.EnumKey(root, i)
        try:
            k = winreg.OpenKey(root, name)
            display = winreg.QueryValueEx(k, "Display")[0]
            tzi = winreg.QueryValueEx(k, "TZI")[0]
        except OSError:
            continue
        dynamic = {}
        try:
            d = winreg.OpenKey(k, "Dynamic DST")
            first, last = winreg.QueryValueEx(d, "FirstEntry")[0], winreg.QueryValueEx(d, "LastEntry")[0]
            for year in range(first, last + 1):
                try:
                    dynamic[year] = winreg.QueryValueEx(d, str(year))[0]
                except OSError:
                    pass
        except OSError:
            pass
        out[name] = {"display": display, "tzi": tzi, "dynamic": dynamic}
    return out


def _transition(year: int, st) -> dt.datetime | None:
    """A SYSTEMTIME rule ("the 5th (=last) Friday of March at 02:00") -> that moment in the given year."""
    w_year, month, weekday, nth, hour, minute = st[0], st[1], st[2], st[3], st[4], st[5]
    if month == 0:
        return None
    if w_year:  # an absolute date
        return dt.datetime(year, month, nth, hour, minute)
    first = dt.date(year, month, 1)
    shift = (weekday - (first.weekday() + 1) % 7) % 7  # Windows: 0 = Sunday
    day = 1 + shift + (nth - 1) * 7
    last_day = (dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)).day
    while day > last_day:
        day -= 7
    if hour == 23 and minute == 59 and st[6] == 59:  # "end of day" rules
        return dt.datetime(year, month, day) + dt.timedelta(days=1)
    return dt.datetime(year, month, day, hour, minute)


def utc_offset_minutes(zone: str, local: dt.datetime) -> int:
    """How many minutes this zone is ahead of UTC at the given local (wall clock) time."""
    info = zones()[zone]
    tzi = info["dynamic"].get(local.year)
    if tzi is None and info["dynamic"]:
        years = sorted(info["dynamic"])
        tzi = info["dynamic"][years[0] if local.year < years[0] else years[-1]]
    bias, std_bias, dst_bias, std, dst = _unpack(tzi or info["tzi"])
    start, end = _transition(local.year, dst), _transition(local.year, std)
    in_dst = False
    if start and end:
        in_dst = start <= local < end if start < end else not (end <= local < start)
    return -(bias + (dst_bias if in_dst else std_bias))


def parse_offset(text: str) -> int | None:
    m = OFFSET_RE.match(text.strip())
    if not m:
        return None
    minutes = int(m.group(2)) * 60 + int(m.group(3) or 0)
    if minutes > 14 * 60:
        return None
    return minutes if m.group(1) == "+" else -minutes


def fmt_offset(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def label(zone: str, now: dt.datetime | None = None) -> str:
    """'(UTC+02:00) Israel · Jerusalem' style label: the English zone name first, then the city as Windows writes it."""
    info = zones().get(zone)
    if not info:
        return zone
    utc_now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    local_guess = utc_now + dt.timedelta(minutes=-(_unpack(info["tzi"])[0]))
    offset = utc_offset_minutes(zone, local_guess)
    short = zone.replace(" Standard Time", "")
    city = english_city(zone)
    text = f"({fmt_offset(offset)}) {city or short}"
    return text[:100]


def search(query: str, limit: int = 25) -> list[tuple[str, str]]:
    """Autocomplete: (label, value) pairs. Value is a Windows zone key, or an offset like 'UTC+03:00'."""
    q = query.strip().lower()
    results: list[tuple[str, str]] = []
    off = parse_offset(q) if q else None
    if off is not None:
        results.append((f"{fmt_offset(off)} (fixed offset, no summer time)", fmt_offset(off)))
    if iana_zones():  # Linux / macOS
        for zone in iana_zones():
            if not q or q.replace(" ", "_") in zone.lower():
                results.append((zone.replace("_", " "), zone))
            if len(results) >= limit:
                break
        return results[:limit]
    seen = set()
    for city, zone in CITIES.items():
        if q and q in city and zone in zones() and zone not in seen:
            seen.add(zone)
            text = label(zone)
            results.append(((text if city in text.lower() else f"{city.title()} — {text}")[:100], zone))
    for zone, info in sorted(zones().items(), key=lambda z: _unpack(z[1]["tzi"])[0], reverse=True):
        if zone in seen:
            continue
        if not q or q in zone.lower() or q in english_city(zone).lower() or q in info["display"].lower():
            seen.add(zone)
            results.append((label(zone), zone))
        if len(results) >= limit:
            break
    return results[:limit]


@lru_cache(maxsize=1)
def iana_zones() -> list[str]:
    """Linux / macOS time zones ('Asia/Jerusalem'), used when Windows' list is not there."""
    if zones():
        return []
    try:
        import zoneinfo

        return sorted(z for z in zoneinfo.available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))
    except Exception:  # noqa: BLE001
        return []


def _iana_offset(zone: str, local: dt.datetime) -> int:
    import zoneinfo

    return int(local.replace(tzinfo=zoneinfo.ZoneInfo(zone)).utcoffset().total_seconds() // 60)


def resolve(value: str) -> tuple[str, str] | None:
    """What the user picked or typed -> (kind, value): ('zone', key) or ('offset', 'UTC+03:00')."""
    v = value.strip()
    if v in zones():
        return "zone", v
    if iana_zones():
        if v in iana_zones():
            return "iana", v
        low = v.lower().replace(" ", "_")
        hit = next((z for z in iana_zones() if z.lower().endswith("/" + low) or z.lower() == low), None)
        if hit:
            return "iana", hit
    off = parse_offset(v)
    if off is not None:
        return "offset", fmt_offset(off)
    hits = search(v, 2)
    hits = [h for h in hits if h[1] in zones()]
    return ("zone", hits[0][1]) if hits else None


def to_utc(kind: str, value: str, local: dt.datetime) -> tuple[dt.datetime, int]:
    """Local wall time in the zone -> (UTC time, offset in minutes)."""
    if kind == "iana":
        offset = _iana_offset(value, local)
    else:
        offset = parse_offset(value) if kind == "offset" else utc_offset_minutes(value, local)
    return local - dt.timedelta(minutes=offset), offset


def now_in(kind: str, value: str) -> dt.datetime:
    utc_now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if kind == "offset":
        return utc_now + dt.timedelta(minutes=parse_offset(value))
    if kind == "iana":
        import zoneinfo

        return dt.datetime.now(zoneinfo.ZoneInfo(value)).replace(tzinfo=None)
    guess = utc_now + dt.timedelta(minutes=-_unpack(zones()[value]["tzi"])[0])
    return utc_now + dt.timedelta(minutes=utc_offset_minutes(value, guess))
