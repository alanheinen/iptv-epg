#!/usr/bin/env python3
"""Build the guide data for the web app.

Pulls every playlist from iptv-org/iptv for the chosen countries (US by
default; --countries us,ca,uk), joins channel metadata, logos and languages
from the iptv-org API, then attaches programme schedules from public XMLTV
feeds (i.mjh.nz for the FAST services, epgshare01.online for cable networks and
local broadcast stations). Writes two files into web/data/:

  channels.json  channel list with streams, logo, categories, source playlist,
                 countries and languages
  guide.json     programmes per channel for a rolling window around now

Standard library only, so it runs anywhere Python 3.9+ does.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import email.utils
import gzip
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

RAW = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/"
CONTENTS = "https://api.github.com/repos/iptv-org/iptv/contents/streams"
API = "https://iptv-org.github.io/api/"

# Countries to build, by iptv-org playlist prefix (so the UK is "uk", not
# "gb"). Set with --countries or the COUNTRIES environment variable, e.g.
# "us,ca,uk". When a channel is in several, the first country listed owns it.
DEFAULT_COUNTRIES = "us"

MJH = "https://i.mjh.nz/"
EPGSHARE = "https://epgshare01.online/epgshare01/epg_ripper_"

# XMLTV feeds per country: kind -> (url, playlists matched against it first).
# A country without an entry here still gets the i.mjh.nz Pluto and Samsung
# feeds for whichever of those playlists it has (see country_feeds).
COUNTRY_FEEDS = {
    "us": {
        "pluto": (MJH + "PlutoTV/us.xml.gz", ["us_pluto"]),
        "samsung": (MJH + "SamsungTVPlus/us.xml.gz", ["us_samsung"]),
        "plex": (MJH + "Plex/us.xml.gz", ["us_plex"]),
        "roku": (MJH + "Roku/all.xml.gz", ["us_roku"]),
        "pbs": (MJH + "PBS/all.xml.gz", ["us_pbs"]),
        # Gracenote-derived listings: cable and satellite networks, sports
        # networks, and every US local station keyed by call sign (KABC-DT...).
        "cable": (EPGSHARE + "US2.xml.gz", ["us"]),
        "sports": (EPGSHARE + "US_SPORTS1.xml.gz", []),
        "locals": (EPGSHARE + "US_LOCALS1.xml.gz", ["us_local"]),
    },
    "ca": {
        "pluto": (MJH + "PlutoTV/ca.xml.gz", ["ca_pluto"]),
        "samsung": (MJH + "SamsungTVPlus/ca.xml.gz", ["ca_samsung"]),
        "plex": (MJH + "Plex/ca.xml.gz", []),
        "cable": (EPGSHARE + "CA2.xml.gz", ["ca"]),
    },
    "uk": {
        "pluto": (MJH + "PlutoTV/gb.xml.gz", ["uk_pluto"]),
        "samsung": (MJH + "SamsungTVPlus/gb.xml.gz", ["uk_samsung"]),
        "plex": (MJH + "Plex/gb.xml.gz", []),
        "cable": (EPGSHARE + "UK1.xml.gz", ["uk", "uk_bbc"]),
    },
}
MJH_REGION = {"uk": "gb"}

# The epgshare01 feeds change about once a day and the US locals one is
# ~60 MB, so the build asks for them with If-Modified-Since and, when they
# haven't changed, reuses what it kept from the last download (see FeedCache).
# They also run days ahead, so the cache keeps a longer window than the guide
# shows.
CACHED_KINDS = {"cable", "sports", "locals"}
CACHE_AHEAD_H = 96

# Name-matching order after a channel's own feed, within its own country:
# broadcast schedules before the FAST services, whose same-named channels run
# different programming. Names are never matched across countries.
FALLBACK_ORDER = ["cable", "sports", "samsung", "plex", "roku", "pluto"]

# Channels the feeds name too differently to match, by iptv-org tvg-id with
# any "HD" dropped from the feed part -> (feed key, XMLTV id). The UK feed
# abbreviates BBC One's regions ("BBC One E Mid HD"); the Canadian one names
# CBC stations by city ("CBC Winnipeg") where iptv-org uses the province, so
# they're keyed by call sign. Left out on purpose, because a neighbouring
# station's schedule would be wrong: CBC New Brunswick and North (no station
# in the feed), and BBC Four/CBeebies and BBC Three/CBBC, which switch between
# two channels' schedules during the day.
LISTING_TABLE = {
    "BBCOne.uk@London": ("cable-uk", "BBC.One.Lon.HD.uk"),
    "BBCOne.uk@ChannelIslands": ("cable-uk", "BBC.One.CI.HD.uk"),
    "BBCOne.uk@East": ("cable-uk", "BBC.One.EastHD.uk"),
    "BBCOne.uk@EastMidlands": ("cable-uk", "BBC.One.E.Mid.HD.uk"),
    "BBCOne.uk@NorthEastCumbria": ("cable-uk", "BBC.One.NE.and.C.HD.uk"),
    "BBCOne.uk@NorthWest": ("cable-uk", "BBC.One.N.West.HD.uk"),
    "BBCOne.uk@NorthernIreland": ("cable-uk", "BBC.One.NI.HD.uk"),
    "BBCOne.uk@Scotland": ("cable-uk", "BBC.One.ScotHD.uk"),
    "BBCOne.uk@South": ("cable-uk", "BBC.One.Sth.HD.uk"),
    "BBCOne.uk@SouthEast": ("cable-uk", "BBC.One.S.East.HD.uk"),
    "BBCOne.uk@SouthWest": ("cable-uk", "BBC.One.S.West.HD.uk"),
    "BBCOne.uk@Wales": ("cable-uk", "BBC.One.Wal.HD.uk"),
    "BBCOne.uk@West": ("cable-uk", "BBC.One.West.HD.uk"),
    "BBCOne.uk@WestMidlands": ("cable-uk", "BBC.One.WM.HD.uk"),
    "BBCOne.uk@Yorkshire": ("cable-uk", "BBC.One.Yorks.HD.uk"),
    # Without these, name matching gives the regions the national BBC Two.
    "BBCTwo.uk@NorthernIreland": ("cable-uk", "BBC.Two.NI.HD.uk"),
    "BBCTwo.uk@Wales": ("cable-uk", "BBC.Two.Wal.HD.uk"),
    "BBCScotland.uk@SD": ("cable-uk", "BBCScotlandHD.uk"),
    "CBCTelevision.ca@CBWTDT": ("cable-ca", "CBC.Winnipeg.HD.ca2"),
    "CBCTelevision.ca@CBMTDT": ("cable-ca", "CBMT.-.CBC.Montreal.ca2"),
    "CBCTelevision.ca@CBNTDT": ("cable-ca", "CBC.St..John\u2019s.ca2"),
    "CBCTelevision.ca@CBHTDT": ("cable-ca", "CBC.Halifax.HD.ca2"),
    "CBCTelevision.ca@CBCTDT": ("cable-ca", "CBCT.ca2"),
    "CBCTelevision.ca@CBETDT": ("cable-ca", "CBC.Television-Windsor.9.ca2"),
}


def table_hit(tvg: str) -> tuple[str, str] | None:
    ch, _, feed = tvg.partition("@")
    return LISTING_TABLE.get(f"{ch}@{feed[:-2] if feed.endswith('HD') else feed}")


# Friendly names for the services, by playlist suffix (us_pluto -> "pluto").
SERVICE_LABELS = {
    "": "Broadcast & Cable", "local": "Local", "pluto": "Pluto TV",
    "samsung": "Samsung TV Plus", "plex": "Plex", "roku": "Roku",
    "tubi": "Tubi", "xumo": "Xumo", "stirr": "STIRR", "pbs": "PBS",
    "distro": "DistroTV", "firetv": "Fire TV", "vizio": "Vizio WatchFree",
    "tcl": "TCL", "cbsn": "CBS News", "abcnews": "ABC News",
    "amagi": "Amagi", "frequency": "Frequency", "wowza": "Wowza",
    "uplynk": "Uplynk", "3abn": "3ABN", "30a": "30A", "bbc": "BBC",
    "stingray": "Stingray", "rakuten": "Rakuten TV", "sportstribal": "Sports Tribal",
}

# Used only when the GitHub API can't list the playlists.
FALLBACK_PLAYLISTS = ["us", "us_local", "us_pluto", "us_samsung", "us_plex", "us_roku", "us_tubi",
                      "us_pbs", "ca", "ca_pluto", "ca_samsung", "uk", "uk_bbc", "uk_pluto", "uk_samsung"]


def country_feeds(cc: str, playlists: list[str]) -> dict[str, tuple[str, list[str]]]:
    if cc in COUNTRY_FEEDS:
        return COUNTRY_FEEDS[cc]
    region = MJH_REGION.get(cc, cc)
    out = {}
    for kind, path in (("pluto", "PlutoTV"), ("samsung", "SamsungTVPlus")):
        if f"{cc}_{kind}" in playlists:
            out[kind] = (f"{MJH}{path}/{region}.xml.gz", [f"{cc}_{kind}"])
    return out


def kind_of(key: str) -> str:
    """Feed key -> kind: "locals-us" -> "locals"."""
    return key.partition("-")[0]


def country_of(source: str) -> str:
    return source.partition("_")[0]


def source_label(source: str) -> str:
    svc = source.partition("_")[2]
    return SERVICE_LABELS.get(svc, svc.title())

WINDOW_BEFORE_H = 3
WINDOW_AFTER_H = 30
DESC_MAX = 320

UA = "iptv-epg-builder/1.0 (+https://github.com/iptv-org/iptv)"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def fetch(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            return data
        except Exception as e:  # noqa: BLE001 - retry any network error
            if attempt == 2:
                raise
            log(f"  retry {url}: {e}")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def norm(name: str) -> str:
    """Normalise a channel name for loose matching across sources."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", s)
    s = s.replace("&", " and ").replace("+", " plus ")
    s = re.sub(r"\b(hd|sd|uhd|4k|east|tv channel)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def json_items(data: bytes):
    """Yield the items of a JSON array one at a time, so a big file is never
    held as a whole list of dicts (which takes ~10x the file's size)."""
    text = data.decode("utf-8")
    del data
    dec, i, n = json.JSONDecoder(), text.index("[") + 1, len(text)
    while True:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n or text[i] == "]":
            return
        obj, i = dec.raw_decode(text, i)
        yield obj


# ---------------------------------------------------------------- call signs

# The locals feed lists each station's main channel, so a match is only made
# for that channel, never a subchannel (-DT2, 25.3), and the call sign comes
# from the tvg-id feed (ABC.us@KRGVTV) or, for the broadcast playlists only,
# the name ("CBS 9 Oklahoma City OK (KWTV-DT1)"). Channels from the FAST
# services are never matched to a station: they carry station-branded news
# and spin-off channels ("WBTV Nikita", Tubi's "ABC 10 San Diego") whose
# programming isn't the broadcast schedule, and their tvg-id feeds are often
# wrong (STIRR's "ABC 2 Portland" is CW.us@KATU). Three-letter call signs (WGN, KYW) only count
# from a name with a suffix, so a word like "WOW" doesn't match a station.
CALL_IN_FEED = re.compile(r"([KW][A-Z]{2,3})(?:TV|LD|CD|DT(\d*))?")
CALL_IN_NAME = re.compile(r"\b([KW][A-Z]{3}|[KW][A-Z]{2}(?=-(?:DT|TV|LD|CD)))(?:-?(?:TV|LD|CD|DT(\d*)))?\b")
CALL_IN_ID = re.compile(r"([KW][A-Z]{2,3})(?:-[A-Z]{1,2}\d*)?\.")
CALL_SOURCES = {"us", "us_local"}
SUBCHANNEL = re.compile(r"\b\d{1,2}\.(?:[2-9]|\d\d)\b")


def call_signs(e: dict) -> list[str]:
    out = []
    if e["source"] not in CALL_SOURCES | {"us_pbs"}:
        return out
    m = CALL_IN_FEED.fullmatch(e["tvg"].partition("@")[2])
    if m and m.group(2) in (None, "", "1"):
        out.append(m.group(1))
    if e["source"] in CALL_SOURCES and not SUBCHANNEL.search(e["name"]):
        out += [c for c, sub in CALL_IN_NAME.findall(e["name"]) if sub in ("", "1")]
    return out


def cid_of(tvg: str) -> str:
    # "CNNInternational.us@SD" -> "CNNInternational"
    return tvg.partition("@")[0].rpartition(".")[0]


PLUTO_ID = re.compile(r"plu-([0-9a-f]{24})|/channel/([0-9a-f]{24})/")


def pluto_id(url: str) -> str:
    m = PLUTO_ID.search(url)
    return (m.group(1) or m.group(2)) if m else ""


def bare_call_name(name: str) -> bool:
    """True for names that are just a station: "WSOC-DT1", "KTTV 11.1"."""
    return not re.sub(r"[KW][A-Z]{2,3}(-?(TV|LD|CD|DT\d*))?|\d+(\.\d+)?|\W", "", name)


# ---------------------------------------------------------------- playlists

EXTINF = re.compile(r'#EXTINF:[^,]*?((?:\s+[\w-]+="[^"]*")*)\s*,(.*)$')
ATTR = re.compile(r'([\w-]+)="([^"]*)"')


def playlist_names(countries: list[str]) -> list[str]:
    """Every playlist for the countries ("us", "us_pluto"...), in country order."""
    names = []
    try:
        names = [i["name"][:-4] for i in json.loads(fetch(CONTENTS)) if i["name"].endswith(".m3u")]
    except Exception as e:  # noqa: BLE001
        log(f"  could not list playlists ({e}); using built-in list")
    names = [n for n in names or FALLBACK_PLAYLISTS if country_of(n) in countries]
    return sorted(names, key=lambda n: (countries.index(country_of(n)), n))


def parse_m3u(text: str, source: str) -> list[dict]:
    out, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            m = EXTINF.match(line)
            if not m:
                cur = None
                continue
            attrs = dict(ATTR.findall(m.group(1)))
            title = m.group(2).strip()
            labels = re.findall(r"\[([^\]]+)\]", title)
            q = re.search(r"\((\d{3,4}p)\)", title)
            name = re.sub(r"\s*\[[^\]]+\]|\s*\(\d{3,4}p\)", "", title).strip()
            cur = {"name": name, "tvg": attrs.get("tvg-id", ""), "q": q.group(1) if q else "",
                   "labels": labels, "source": source, "cc": country_of(source), "ref": "", "ua": ""}
        elif line.startswith("#EXTVLCOPT:") and cur is not None:
            k, _, v = line[11:].partition("=")
            if k == "http-referrer":
                cur["ref"] = v
            elif k == "http-user-agent":
                cur["ua"] = v
        elif line and not line.startswith("#") and cur is not None:
            cur["url"] = line
            out.append(cur)
            cur = None
    return out


# ---------------------------------------------------------------- xmltv

def xmltime(s: str) -> int:
    """XMLTV timestamp -> unix seconds."""
    s = s.strip()
    dt = datetime.strptime(s[:14], "%Y%m%d%H%M%S")
    off = s[14:].strip()
    secs = int(dt.replace(tzinfo=timezone.utc).timestamp())
    if off:
        sign = 1 if off[0] == "+" else -1
        secs -= sign * (int(off[1:3]) * 3600 + int(off[3:5]) * 60)
    return secs


def parse_xmltv(src, lo: int, hi: int, keep=None):
    """Return ({id: [display names]}, {id: [programmes]}) inside [lo, hi].

    `src` is bytes or a binary file object; a file is parsed as it streams, so
    a feed far bigger than memory still builds. With `keep(id, display_names)`,
    only the channels it accepts are kept, with their programmes. XMLTV lists
    the channels first; a programme for an undeclared channel is judged by its
    id alone."""
    names: dict[str, list[str]] = {}
    progs: dict[str, list] = {}
    verdict: dict[str, bool] = {}
    if isinstance(src, bytes):
        src = io.BytesIO(src)
    for _, el in ET.iterparse(src, events=("end",)):
        if el.tag == "channel":
            cid = el.get("id")
            dns = [d.text or "" for d in el.findall("display-name")]
            verdict[cid] = keep is None or bool(keep(cid, dns))
            if verdict[cid]:
                names[cid] = dns
            el.clear()
        elif el.tag == "programme" and keep is not None and not verdict.setdefault(
                el.get("channel"), bool(keep(el.get("channel"), []))):
            el.clear()
        elif el.tag == "programme":
            try:
                start, stop = xmltime(el.get("start")), xmltime(el.get("stop"))
            except (TypeError, ValueError):
                el.clear()
                continue
            if stop >= lo and start <= hi:
                title = (el.findtext("title") or "").strip()
                sub = (el.findtext("sub-title") or "").strip()
                if sub == title:
                    sub = ""
                desc = (el.findtext("desc") or "").strip()
                if len(desc) > DESC_MAX:
                    desc = desc[:DESC_MAX].rsplit(" ", 1)[0] + "…"
                ep = ""
                for e in el.findall("episode-num"):
                    if e.get("system") == "onscreen":
                        ep = (e.text or "").strip()
                cat = el.findtext("category") or ""
                p = [start // 60, stop // 60, title]
                extra = [sub, desc, ep, cat.strip()]
                while extra and not extra[-1]:
                    extra.pop()
                progs.setdefault(el.get("channel"), []).append(p + extra)
            el.clear()
    for v in progs.values():
        v.sort(key=lambda p: p[0])
    return names, progs


class FeedCache:
    """What the build keeps of a big feed between runs: its Last-Modified, the
    display names, and the programmes of the channels it matched last time.

    A channel that newly appears in iptv-org can't match a cached feed until
    the feed changes upstream (about daily) and is downloaded in full again."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def lastmod(self, key: str) -> str:
        return self.data.get(key, {}).get("lastmod", "")

    def get(self, key: str, lo: int):
        d = self.data.get(key)
        if not d:
            return None
        progs = {cid: [p for p in v if p[1] * 60 >= lo] for cid, v in d["progs"].items()}
        return {cid: d["names"][cid] for cid in progs}, progs

    def put(self, key: str, lastmod: str, names: dict, progs: dict, used: set) -> None:
        self.data[key] = {
            "lastmod": lastmod,
            "names": {cid: names.get(cid, []) for cid in used if cid in progs},
            "progs": {cid: progs[cid] for cid in used if cid in progs},
        }

    def prune(self, keys: set) -> bool:
        stale = [k for k in self.data if k not in keys]
        for k in stale:
            del self.data[k]
        return bool(stale)

    def save(self) -> None:
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(self.data, separators=(",", ":"), ensure_ascii=False))
        tmp.replace(self.path)


def load_feed(key: str, url: str, lo: int, hi: int, cache: FeedCache, keep=None):
    """Parse one XMLTV feed. Returns (names, progs, lastmod or None); lastmod is
    set only when a cacheable feed was downloaded fresh."""
    cached = kind_of(key) in CACHED_KINDS
    h = {"User-Agent": UA}
    if cached and cache.lastmod(key) and cache.get(key, lo):
        h["If-Modified-Since"] = cache.lastmod(key)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=180) as r:
                lastmod = r.headers.get("Last-Modified") or email.utils.formatdate(usegmt=True)
                names, progs = parse_xmltv(gzip.GzipFile(fileobj=r) if url.endswith(".gz") else r, lo, hi, keep)
            return names, progs, lastmod if cached else None
        except urllib.error.HTTPError as e:
            if e.code == 304:
                names, progs = cache.get(key, lo)
                return names, progs, None
            err = e
            if e.code == 404:  # no feed for this country; retrying won't help
                break
        except Exception as e:  # noqa: BLE001 - retry any network or parse error
            err = e
        if attempt < 2:
            log(f"  retry {key}: {err}")
            time.sleep(2 * (attempt + 1))
    # Out of retries: fall back to what the last good download left, if anything.
    old = cache.get(key, lo) if cached else None
    if old:
        log(f"  {key}: failed ({err}); using the cached copy")
        return old[0], old[1], None
    raise err


# ---------------------------------------------------------------- build

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "web" / "data"))
    ap.add_argument("--no-epg", action="store_true", help="skip programme data")
    ap.add_argument("--countries", default=os.environ.get("COUNTRIES") or DEFAULT_COUNTRIES,
                    help="comma-separated iptv-org playlist prefixes, e.g. us,ca,uk (default: $COUNTRIES or us)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    countries = list(dict.fromkeys(c.strip().lower() for c in args.countries.split(",") if c.strip()))
    bad = [c for c in countries if not re.fullmatch(r"[a-z]{2}", c)]
    if bad or not countries:
        ap.error(f"--countries takes two-letter playlist prefixes like us,ca,uk (got {args.countries!r})")

    now = int(time.time())
    lo, hi = now - WINDOW_BEFORE_H * 3600, now + WINDOW_AFTER_H * 3600

    log(f"playlists for {', '.join(countries)}…")
    names = playlist_names(countries)
    with cf.ThreadPoolExecutor(8) as ex:
        texts = dict(zip(names, ex.map(lambda n: fetch(RAW + n + ".m3u").decode("utf-8", "replace"), names)))
    entries = [e for n in names for e in parse_m3u(texts[n], n)]
    log(f"  {len(names)} playlists, {len(entries)} streams")
    missing = [c for c in countries if not any(country_of(n) == c for n in names)]
    if missing:
        log(f"  no iptv-org playlists for {', '.join(missing)}")

    # Feed key "<kind>-<country>": "pluto-us", "cable-ca".
    feeds = {f"{kind}-{cc}": (url, srcs) for cc in countries
             for kind, (url, srcs) in country_feeds(cc, names).items()}

    log("iptv-org metadata…")
    # The API files cover every channel in the world (guides.json alone is
    # ~25 MB); read them a record at a time and keep only the playlists' ones.
    tagged = {e["tvg"].partition("@")[0] for e in entries}
    meta = {c["id"]: c for c in json_items(fetch(API + "channels.json")) if c["id"] in tagged}
    cats = {c["id"]: c["name"] for c in json.loads(fetch(API + "categories.json"))}
    # Languages are per feed (CNN.us@SD); a tvg-id without one takes the main feed's.
    feed_langs: dict[tuple, list] = {}
    for f in json_items(fetch(API + "feeds.json")):
        if f["channel"] not in tagged:
            continue
        feed_langs[(f["channel"], f["id"])] = f.get("languages") or []
        if f.get("is_main"):
            feed_langs.setdefault((f["channel"], None), f.get("languages") or [])
    lang_names = {l["code"]: l["name"] for l in json.loads(fetch(API + "languages.json"))}
    country_info = {c["code"].lower(): c for c in json.loads(fetch(API + "countries.json"))}
    logos: dict[str, dict] = {}
    for l in json_items(fetch(API + "logos.json")):
        if l["channel"] not in tagged:
            continue
        key = (l["channel"], l.get("feed"))
        best = logos.get(key)
        # Prefer in-use, then square-ish, then larger logos.
        score = (l.get("in_use", False), -abs((l.get("width") or 1) / max(l.get("height") or 1, 1) - 1.5), l.get("width") or 0)
        if best is None or score > best["_s"]:
            logos[key] = {**l, "_s": score}

    # iptv-org's own guide mappings (channel/feed -> site + site_id), used as a
    # last resort for channels nothing else matched. Only the sites whose data
    # this build already downloads are usable: i.mjh.nz feeds and plex.tv.
    try:
        guides = [g for g in json_items(fetch(API + "guides.json"))
                  if g.get("channel") in tagged and g.get("site") in ("i.mjh.nz", "plex.tv")]
    except Exception as e:  # noqa: BLE001 - optional; matching works without it
        log(f"  guides.json unavailable ({e})")
        guides = []
    # "PlutoTV/us" -> "pluto-us", for the i.mjh.nz feeds being fetched.
    mjh_keys = {url[len(MJH):-len(".xml.gz")]: key for key, (url, _) in feeds.items() if url.startswith(MJH)}
    mapped: dict[tuple, list] = {}
    for g in guides:
        if g.get("site") == "i.mjh.nz":
            feed_name, _, sid = (g.get("site_id") or "").partition("#")
            if feed_name in mjh_keys:
                hit = (mjh_keys[feed_name], sid)
            else:
                continue
        elif g.get("site") == "plex.tv":
            hit = ("plex-suffix", g.get("site_id") or "")
        else:
            continue
        for k in ((g["channel"], g.get("feed")), (g["channel"], None)):
            mapped.setdefault(k, []).append(hit)

    epg_names: dict[str, dict] = {}
    epg_progs: dict[str, dict] = {}
    fresh: dict[str, str] = {}  # cacheable feeds downloaded this run -> Last-Modified
    cache = FeedCache(out / "feed-cache.json")
    if not args.no_epg:
        log("xmltv feeds…")
        cache_hi = now + CACHE_AHEAD_H * 3600
        wanted = {c for e in entries for c in call_signs(e)}
        # What any match below could look for, so each feed holds only the
        # channels that might match: most of a feed's channels are never used,
        # and keeping their programmes doubled the build's memory.
        want_names: dict[str, set] = {cc: set() for cc in countries}
        for e in entries:
            want_names[e["cc"]].update(n for n in (norm(e["name"]), norm(cid_of(e["tvg"]))) if n)
        pluto_ids = {pluto_id(e["url"]) for e in entries} - {""}
        pbs_calls = {c for e in entries if e["cc"] == "us"
                     for f in [e["tvg"].partition("@")[2]] for c in (f, re.sub(r"(TV|DT\d*)$", "", f)) if c}
        mapped_ids: dict[str, set] = {}
        for k, sid in [h for hits in mapped.values() for h in hits] + list(LISTING_TABLE.values()):
            mapped_ids.setdefault(k, set()).add(sid)

        def keep_for(key: str):
            kind, cc = kind_of(key), key.partition("-")[2]
            if key == "locals-us":  # 4,000+ stations; hold only ones a playlist names
                return lambda cid, _: bool((m := CALL_IN_ID.match(cid or "")) and m.group(1) in wanted)
            names_cc, ids = want_names[cc], mapped_ids.get(key, set())
            return lambda cid, dns: bool(
                cid in ids
                or (kind == "pluto" and cid in pluto_ids)
                or (kind == "pbs" and cid in pbs_calls)
                or (kind == "plex" and (cid or "").rpartition("-")[2] in mapped_ids.get("plex-suffix", ()))
                or any(norm(d) in names_cc for d in dns))

        def load(item):
            key, (url, _) = item
            try:
                keep = keep_for(key)
                n, p, lastmod = load_feed(key, url, lo, cache_hi if kind_of(key) in CACHED_KINDS else hi, cache, keep)
                return key, n, p, lastmod
            except Exception as e:  # noqa: BLE001
                log(f"  {key}: failed ({e})")
                return key, {}, {}, None

        with cf.ThreadPoolExecutor(4) as ex:
            for key, n, p, lastmod in ex.map(load, feeds.items()):
                epg_names[key], epg_progs[key] = n, p
                if lastmod:
                    fresh[key] = lastmod
                how = "downloaded" if lastmod else "cached" if kind_of(key) in CACHED_KINDS else "downloaded"
                log(f"  {key}: {len(n)} channels, {sum(map(len, p.values()))} programmes ({how})")

    # Local stations by call sign: KABC-DT.us_locals1 -> KABC.
    calls: dict[str, str] = {}
    for cid in sorted(epg_progs.get("locals-us", {}), key=lambda c: (not re.match(r"[KW][A-Z0-9]+-(DT|TV)\.", c), c)):
        m = CALL_IN_ID.match(cid)
        if m:
            calls.setdefault(m.group(1), cid)

    # name index per feed: normalised display name -> xmltv id (only ids with programmes)
    name_idx = {k: {} for k in epg_names}
    for k, chans in epg_names.items():
        # East-coast feeds win over Pacific/West ones that normalise the same.
        for cid, dns in sorted(chans.items(), key=lambda kv: bool(re.search(r"pacific|west", kv[0], re.I))):
            if cid not in epg_progs[k]:
                continue
            for dn in dns:
                n = norm(dn)
                if n and len(n) > 2:
                    name_idx[k].setdefault(n, cid)

    # i.mjh.nz's Plex ids are "<plex-region-id>-<plex.tv channel id>".
    plex_by_suffix: dict[str, tuple[str, str]] = {}
    for k in (k for k in feeds if kind_of(k) == "plex"):
        for cid in epg_progs.get(k, {}):
            plex_by_suffix.setdefault(cid.rpartition("-")[2], (k, cid))
    pluto_keys = [k for k in feeds if kind_of(k) == "pluto"]

    def match_mapped(e: dict) -> tuple[str, str] | None:
        cid_meta, _, feed = e["tvg"].partition("@")
        for k, sid in mapped.get((cid_meta, feed or None), []) + mapped.get((cid_meta, None), []):
            if k == "plex-suffix":
                k, sid = plex_by_suffix.get(sid, ("", ""))
            if sid and sid in epg_progs.get(k, {}):
                return k, sid
        return None

    def match_epg(e: dict) -> tuple[str, str] | None:
        cc = e["cc"]
        # A Pluto channel id is the same channel in any region's feed.
        pid = pluto_id(e["url"])
        if pid:
            for k in sorted(pluto_keys, key=lambda k: k != f"pluto-{cc}"):
                if pid in epg_progs.get(k, {}):
                    return k, pid
        hit = table_hit(e["tvg"])
        if hit and hit[1] in epg_progs.get(hit[0], {}):
            return hit
        feed = e["tvg"].partition("@")[2]
        for call in (feed, re.sub(r"(TV|DT\d*)$", "", feed)):
            if call and cc == "us" and call in epg_progs.get("pbs-us", {}):
                return "pbs-us", call
        own = [k for k, (_, srcs) in feeds.items() if e["source"] in srcs and kind_of(k) != "locals"]
        n = norm(e["name"])
        base = norm(cid_of(e["tvg"]))
        order = own + [k for k in (f"{kind}-{cc}" for kind in FALLBACK_ORDER) if k in feeds and k not in own]
        for k in own:
            cid = name_idx.get(k, {}).get(n)
            if cid:
                return k, cid
        for call in call_signs(e):
            if call in calls:
                return "locals-us", calls[call]
        for key in (n, base):
            if not key:
                continue
            for k in order:
                cid = name_idx.get(k, {}).get(key)
                if cid:
                    return k, cid
        return match_mapped(e)

    log("grouping…")
    channels: dict[str, dict] = {}
    guide: dict[str, list] = {}
    for e in entries:
        tvg = e["tvg"]
        cid_meta, _, feed = tvg.partition("@")
        # Untagged channels are keyed by name, and outside the US by country
        # too, so the UK's and Canada's same-named Pluto channels stay apart.
        key = tvg or "n:" + ("" if e["cc"] == "us" else e["cc"] + ":") + norm(e["name"])
        if not key or key.endswith(":"):
            continue
        ch = channels.get(key)
        if ch is None:
            m = meta.get(cid_meta, {})
            logo = logos.get((cid_meta, feed)) or logos.get((cid_meta, None))
            ch = channels[key] = {
                "id": key,
                "name": e["name"],
                "logo": logo["url"] if logo else "",
                "cats": [cats.get(c, c) for c in m.get("categories", [])],
                "network": m.get("network") or "",
                "site": m.get("website") or "",
                "nsfw": bool(m.get("is_nsfw")),
                "src": e["source"],
                "cc": [],
                "langs": feed_langs.get((cid_meta, feed or None)) or feed_langs.get((cid_meta, None)) or [] if cid_meta else [],
                "streams": [],
                "epg": None,
            }
        if e["cc"] not in ch["cc"]:
            ch["cc"].append(e["cc"])
        if any(s["url"] == e["url"] for s in ch["streams"]):
            continue
        s = {"url": e["url"]}
        for k in ("q", "ref", "ua"):
            if e[k]:
                s[k] = e[k]
        if e["labels"]:
            s["tags"] = e["labels"]
        ch["streams"].append(s)
        if ch["epg"] is None:
            hit = match_epg(e)
            if hit:
                ch["epg"] = f"{hit[0]}:{hit[1]}"
                guide[key] = epg_progs[hit[0]][hit[1]]

    # Order streams: secure first, then by resolution, flagged ones last.
    def stream_rank(s):
        q = int(s.get("q", "0p")[:-1] or 0)
        return (bool(s.get("tags")), not s["url"].startswith("https"), -q)

    if not args.no_epg:
        used: dict[str, set] = {k: set() for k in feeds if kind_of(k) in CACHED_KINDS}
        for ch in channels.values():
            if ch["epg"]:
                k, _, cid = ch["epg"].partition(":")
                if k in used:
                    used[k].add(cid)
        for k in used:
            if k in fresh:
                cache.put(k, fresh[k], epg_names[k], epg_progs[k], used[k])
        # Drop feeds no longer built (a country taken out of COUNTRIES).
        if cache.prune(set(used)) or fresh:
            cache.save()
        # Several channels on one station's listings are spin-offs or a
        # multi-channel low-power station (KPVM-LD carries eight networks);
        # keep only a channel that is plainly the station itself.
        by_station: dict[str, list] = {}
        for ch in channels.values():
            if (ch["epg"] or "").startswith("locals-us:"):
                by_station.setdefault(ch["epg"], []).append(ch)
        for group in by_station.values():
            if len(group) > 1:
                for ch in group:
                    if not bare_call_name(ch["name"]):
                        ch["epg"] = None
                        guide.pop(ch["id"], None)
        # The cached feeds were parsed with a longer window; trim to the guide's.
        for key, ch_id in [(c["epg"].partition(":")[0], c["id"]) for c in channels.values() if c["epg"]]:
            if kind_of(key) in CACHED_KINDS:
                guide[ch_id] = [p for p in guide[ch_id] if p[0] * 60 <= hi]

    chan_list = []
    for ch in channels.values():
        if ch["nsfw"]:
            continue
        ch["streams"].sort(key=stream_rank)
        ch.pop("nsfw")
        chan_list.append(ch)
    chan_list.sort(key=lambda c: (c["epg"] is None, c["name"].lower()))

    sources: dict[str, int] = {}
    in_country: dict[str, int] = {}
    in_lang: dict[str, int] = {}
    for c in chan_list:
        sources[c["src"]] = sources.get(c["src"], 0) + 1
        for cc in c["cc"]:
            in_country[cc] = in_country.get(cc, 0) + 1
        for l in c["langs"]:
            in_lang[l] = in_lang.get(l, 0) + 1

    built = datetime.now(timezone.utc).isoformat(timespec="seconds")
    def write(name: str, obj) -> None:
        # Write then rename, so a server reading the directory never sees half a file.
        tmp = out / f".{name}.tmp"
        tmp.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))
        tmp.replace(out / name)

    write("channels.json", {
        "built": built,
        "sources": [{"id": k, "label": source_label(k), "cc": country_of(k), "count": v}
                    for k, v in sorted(sources.items(), key=lambda kv: (countries.index(country_of(kv[0])), -kv[1]))],
        "countries": [{"id": cc, "name": country_info.get(cc, {}).get("name", cc.upper()),
                       "flag": country_info.get(cc, {}).get("flag", ""), "count": in_country[cc]}
                      for cc in countries if cc in in_country],
        "languages": [{"id": l, "name": lang_names.get(l, l), "count": n}
                      for l, n in sorted(in_lang.items(), key=lambda kv: -kv[1])],
        "channels": chan_list,
    })
    write("guide.json", {"built": built, "from": lo // 60, "to": hi // 60, "guide": guide})

    with_epg = sum(1 for c in chan_list if c["epg"])
    log(f"done: {len(chan_list)} channels, {with_epg} with guide data -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
