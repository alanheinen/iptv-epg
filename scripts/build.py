#!/usr/bin/env python3
"""Build the guide data for the web app.

Pulls every US playlist from iptv-org/iptv, joins channel metadata and logos
from the iptv-org API, then attaches programme schedules from public XMLTV
feeds (i.mjh.nz for the FAST services, epgshare01.online for cable networks and
local broadcast stations). Writes two files into web/data/:

  channels.json  channel list with streams, logo, categories, source playlist
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

# XMLTV feeds and the playlists whose names they should be matched against first.
EPG_FEEDS = {
    "pluto": ("https://i.mjh.nz/PlutoTV/us.xml.gz", ["us_pluto"]),
    "samsung": ("https://i.mjh.nz/SamsungTVPlus/us.xml.gz", ["us_samsung"]),
    "plex": ("https://i.mjh.nz/Plex/us.xml.gz", ["us_plex"]),
    "roku": ("https://i.mjh.nz/Roku/all.xml.gz", ["us_roku"]),
    "pbs": ("https://i.mjh.nz/PBS/all.xml.gz", ["us_pbs"]),
    # Gracenote-derived listings: cable and satellite networks, sports
    # networks, and every US local station keyed by call sign (KABC-DT...).
    "cable": ("https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz", ["us"]),
    "sports": ("https://epgshare01.online/epgshare01/epg_ripper_US_SPORTS1.xml.gz", []),
    "locals": ("https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz", ["us_local"]),
}

# The epgshare01 feeds change about once a day and the locals one is ~60 MB, so
# the build asks for them with If-Modified-Since and, when they haven't changed,
# reuses what it kept from the last download (see FeedCache). They also run
# days ahead, so the cache keeps a longer window than the guide shows.
CACHED_FEEDS = {"cable", "sports", "locals"}
CACHE_AHEAD_H = 96

# Name-matching order after a channel's own feed: broadcast schedules before
# the FAST services, whose same-named channels run different programming.
FALLBACK_ORDER = ["cable", "sports", "samsung", "plex", "roku", "pluto"]

# Friendly names for the playlist files.
SOURCE_LABELS = {
    "us": "Broadcast & Cable", "us_local": "Local", "us_pluto": "Pluto TV",
    "us_samsung": "Samsung TV Plus", "us_plex": "Plex", "us_roku": "Roku",
    "us_tubi": "Tubi", "us_xumo": "Xumo", "us_stirr": "STIRR", "us_pbs": "PBS",
    "us_distro": "DistroTV", "us_firetv": "Fire TV", "us_vizio": "Vizio WatchFree",
    "us_tcl": "TCL", "us_cbsn": "CBS News", "us_abcnews": "ABC News",
    "us_amagi": "Amagi", "us_frequency": "Frequency", "us_wowza": "Wowza",
    "us_uplynk": "Uplynk", "us_3abn": "3ABN", "us_30a": "30A",
}

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


def bare_call_name(name: str) -> bool:
    """True for names that are just a station: "WSOC-DT1", "KTTV 11.1"."""
    return not re.sub(r"[KW][A-Z]{2,3}(-?(TV|LD|CD|DT\d*))?|\d+(\.\d+)?|\W", "", name)


# ---------------------------------------------------------------- playlists

EXTINF = re.compile(r'#EXTINF:[^,]*?((?:\s+[\w-]+="[^"]*")*)\s*,(.*)$')
ATTR = re.compile(r'([\w-]+)="([^"]*)"')


def playlist_names() -> list[str]:
    try:
        items = json.loads(fetch(CONTENTS))
        names = [i["name"][:-4] for i in items
                 if re.fullmatch(r"us(_\w+)?\.m3u", i["name"])]
        if names:
            return sorted(names)
    except Exception as e:  # noqa: BLE001
        log(f"  could not list playlists ({e}); using built-in list")
    return sorted(SOURCE_LABELS)


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
                   "labels": labels, "source": source, "ref": "", "ua": ""}
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
    a feed far bigger than memory still builds. With `keep`, only channels it
    accepts are kept."""
    names: dict[str, list[str]] = {}
    progs: dict[str, list] = {}
    if isinstance(src, bytes):
        src = io.BytesIO(src)
    for _, el in ET.iterparse(src, events=("end",)):
        if keep and el.tag in ("channel", "programme") and not keep(el.get("id") or el.get("channel")):
            el.clear()
        elif el.tag == "channel":
            names[el.get("id")] = [d.text or "" for d in el.findall("display-name")]
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

    def save(self) -> None:
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(self.data, separators=(",", ":"), ensure_ascii=False))
        tmp.replace(self.path)


def load_feed(key: str, url: str, lo: int, hi: int, cache: FeedCache, keep=None):
    """Parse one XMLTV feed. Returns (names, progs, lastmod or None); lastmod is
    set only when a cacheable feed was downloaded fresh."""
    cached = key in CACHED_FEEDS
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
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    now = int(time.time())
    lo, hi = now - WINDOW_BEFORE_H * 3600, now + WINDOW_AFTER_H * 3600

    log("playlists…")
    names = playlist_names()
    with cf.ThreadPoolExecutor(8) as ex:
        texts = dict(zip(names, ex.map(lambda n: fetch(RAW + n + ".m3u").decode("utf-8", "replace"), names)))
    entries = [e for n in names for e in parse_m3u(texts[n], n)]
    log(f"  {len(names)} playlists, {len(entries)} streams")

    log("iptv-org metadata…")
    meta = {c["id"]: c for c in json.loads(fetch(API + "channels.json"))}
    cats = {c["id"]: c["name"] for c in json.loads(fetch(API + "categories.json"))}
    logos: dict[str, dict] = {}
    for l in json.loads(fetch(API + "logos.json")):
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
        guides = json.loads(fetch(API + "guides.json"))
    except Exception as e:  # noqa: BLE001 - optional; matching works without it
        log(f"  guides.json unavailable ({e})")
        guides = []
    MJH = {"PlutoTV/us": "pluto", "SamsungTVPlus/us": "samsung", "Plex/us": "plex", "Roku/all": "roku", "PBS/all": "pbs"}
    mapped: dict[tuple, list] = {}
    for g in guides:
        if g.get("site") == "i.mjh.nz":
            feed_name, _, sid = (g.get("site_id") or "").partition("#")
            if feed_name in MJH:
                hit = (MJH[feed_name], sid)
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

        def load(item):
            key, (url, _) = item
            try:
                keep = None
                if key == "locals":  # 4,000+ stations; hold only ones a playlist names
                    keep = lambda cid: bool((m := CALL_IN_ID.match(cid or "")) and m.group(1) in wanted)
                n, p, lastmod = load_feed(key, url, lo, cache_hi if key in CACHED_FEEDS else hi, cache, keep)
                return key, n, p, lastmod
            except Exception as e:  # noqa: BLE001
                log(f"  {key}: failed ({e})")
                return key, {}, {}, None

        with cf.ThreadPoolExecutor(4) as ex:
            for key, n, p, lastmod in ex.map(load, EPG_FEEDS.items()):
                epg_names[key], epg_progs[key] = n, p
                if lastmod:
                    fresh[key] = lastmod
                how = "downloaded" if lastmod else "cached" if key in CACHED_FEEDS else "downloaded"
                log(f"  {key}: {len(n)} channels, {sum(map(len, p.values()))} programmes ({how})")

    # Local stations by call sign: KABC-DT.us_locals1 -> KABC.
    calls: dict[str, str] = {}
    for cid in sorted(epg_progs.get("locals", {}), key=lambda c: (not re.match(r"[KW][A-Z0-9]+-(DT|TV)\.", c), c)):
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
    plex_by_suffix = {cid.rpartition("-")[2]: cid for cid in epg_progs.get("plex", {})}

    def match_mapped(e: dict) -> tuple[str, str] | None:
        cid_meta, _, feed = e["tvg"].partition("@")
        for k, sid in mapped.get((cid_meta, feed or None), []) + mapped.get((cid_meta, None), []):
            if k == "plex-suffix":
                k, sid = "plex", plex_by_suffix.get(sid, "")
            if sid and sid in epg_progs.get(k, {}):
                return k, sid
        return None

    def match_epg(e: dict) -> tuple[str, str] | None:
        m = re.search(r"plu-([0-9a-f]{24})", e["url"]) or re.search(r"/channel/([0-9a-f]{24})/", e["url"])
        if m and m.group(1) in epg_progs.get("pluto", {}):
            return "pluto", m.group(1)
        feed = e["tvg"].partition("@")[2]
        for call in (feed, re.sub(r"(TV|DT\d*)$", "", feed)):
            if call and call in epg_progs.get("pbs", {}):
                return "pbs", call
        own = [k for k, (_, srcs) in EPG_FEEDS.items() if e["source"] in srcs and k != "locals"]
        n = norm(e["name"])
        base = norm(cid_of(e["tvg"]))
        order = own + [k for k in FALLBACK_ORDER if k not in own]
        for k in own:
            cid = name_idx.get(k, {}).get(n)
            if cid:
                return k, cid
        for call in call_signs(e):
            if call in calls:
                return "locals", calls[call]
        for key in (n, base):
            if not key:
                continue
            for k in order:
                cid = name_idx.get(k, {}).get(key)
                if cid:
                    return k, cid
        return match_mapped(e)

    def cid_of(tvg: str) -> str:
        # "CNNInternational.us@SD" -> "CNNInternational"
        return tvg.partition("@")[0].rpartition(".")[0]

    log("grouping…")
    channels: dict[str, dict] = {}
    guide: dict[str, list] = {}
    for e in entries:
        tvg = e["tvg"]
        cid_meta, _, feed = tvg.partition("@")
        key = tvg or "n:" + norm(e["name"])
        if not key or key == "n:":
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
                "streams": [],
                "epg": None,
            }
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
        used: dict[str, set] = {k: set() for k in CACHED_FEEDS}
        for ch in channels.values():
            if ch["epg"]:
                k, _, cid = ch["epg"].partition(":")
                if k in used:
                    used[k].add(cid)
        for k in CACHED_FEEDS:
            if k in fresh:
                cache.put(k, fresh[k], epg_names[k], epg_progs[k], used[k])
        if fresh:
            cache.save()
        # Several channels on one station's listings are spin-offs or a
        # multi-channel low-power station (KPVM-LD carries eight networks);
        # keep only a channel that is plainly the station itself.
        by_station: dict[str, list] = {}
        for ch in channels.values():
            if (ch["epg"] or "").startswith("locals:"):
                by_station.setdefault(ch["epg"], []).append(ch)
        for group in by_station.values():
            if len(group) > 1:
                for ch in group:
                    if not bare_call_name(ch["name"]):
                        ch["epg"] = None
                        guide.pop(ch["id"], None)
        # The cached feeds were parsed with a longer window; trim to the guide's.
        for key, ch_id in [(c["epg"].partition(":")[0], c["id"]) for c in channels.values() if c["epg"]]:
            if key in CACHED_FEEDS:
                guide[ch_id] = [p for p in guide[ch_id] if p[0] * 60 <= hi]

    chan_list = []
    for ch in channels.values():
        if ch["nsfw"]:
            continue
        ch["streams"].sort(key=stream_rank)
        ch.pop("nsfw")
        chan_list.append(ch)
    chan_list.sort(key=lambda c: (c["epg"] is None, c["name"].lower()))

    sources = {}
    for c in chan_list:
        sources[c["src"]] = sources.get(c["src"], 0) + 1

    built = datetime.now(timezone.utc).isoformat(timespec="seconds")
    def write(name: str, obj) -> None:
        # Write then rename, so a server reading the directory never sees half a file.
        tmp = out / f".{name}.tmp"
        tmp.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))
        tmp.replace(out / name)

    write("channels.json", {
        "built": built,
        "sources": [{"id": k, "label": SOURCE_LABELS.get(k, k.removeprefix("us_").title()), "count": v}
                    for k, v in sorted(sources.items(), key=lambda kv: -kv[1])],
        "channels": chan_list,
    })
    write("guide.json", {"built": built, "from": lo // 60, "to": hi // 60, "guide": guide})

    with_epg = sum(1 for c in chan_list if c["epg"])
    log(f"done: {len(chan_list)} channels, {with_epg} with guide data -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
