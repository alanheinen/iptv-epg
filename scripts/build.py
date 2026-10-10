#!/usr/bin/env python3
"""Build the guide data for the web app.

Pulls every US playlist from iptv-org/iptv, joins channel metadata and logos
from the iptv-org API, then attaches programme schedules from public XMLTV
feeds (i.mjh.nz). Writes two files into web/data/:

  channels.json  channel list with streams, logo, categories, source playlist
  guide.json     programmes per channel for a rolling window around now

Standard library only, so it runs anywhere Python 3.9+ does.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import json
import re
import sys
import time
import unicodedata
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
}

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


def parse_xmltv(data: bytes, lo: int, hi: int):
    """Return ({id: [display names]}, {id: [programmes]}) inside [lo, hi]."""
    names: dict[str, list[str]] = {}
    progs: dict[str, list] = {}
    for _, el in ET.iterparse(__import__("io").BytesIO(data), events=("end",)):
        if el.tag == "channel":
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

    epg_names: dict[str, dict] = {}
    epg_progs: dict[str, dict] = {}
    if not args.no_epg:
        log("xmltv feeds…")

        def load(item):
            key, (url, _) = item
            try:
                return key, parse_xmltv(fetch(url, timeout=180), lo, hi)
            except Exception as e:  # noqa: BLE001
                log(f"  {key}: failed ({e})")
                return key, ({}, {})

        with cf.ThreadPoolExecutor(5) as ex:
            for key, (n, p) in ex.map(load, EPG_FEEDS.items()):
                epg_names[key], epg_progs[key] = n, p
                log(f"  {key}: {len(n)} channels, {sum(map(len, p.values()))} programmes")

    # name index per feed: normalised display name -> xmltv id (only ids with programmes)
    name_idx = {k: {} for k in epg_names}
    for k, chans in epg_names.items():
        for cid, dns in chans.items():
            if cid not in epg_progs[k]:
                continue
            for dn in dns:
                n = norm(dn)
                if n and len(n) > 2:
                    name_idx[k].setdefault(n, cid)

    def match_epg(e: dict) -> tuple[str, str] | None:
        m = re.search(r"plu-([0-9a-f]{24})", e["url"]) or re.search(r"/channel/([0-9a-f]{24})/", e["url"])
        if m and m.group(1) in epg_progs.get("pluto", {}):
            return "pluto", m.group(1)
        feed = e["tvg"].partition("@")[2]
        for call in (feed, re.sub(r"(TV|DT\d*)$", "", feed)):
            if call and call in epg_progs.get("pbs", {}):
                return "pbs", call
        n = norm(e["name"])
        if not n:
            return None
        order = [k for k, (_, srcs) in EPG_FEEDS.items() if e["source"] in srcs]
        order += [k for k in ("samsung", "plex", "roku", "pluto") if k not in order]
        for k in order:
            cid = name_idx.get(k, {}).get(n)
            if cid:
                return k, cid
        return None

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
    (out / "channels.json").write_text(json.dumps({
        "built": built,
        "sources": [{"id": k, "label": SOURCE_LABELS.get(k, k.removeprefix("us_").title()), "count": v}
                    for k, v in sorted(sources.items(), key=lambda kv: -kv[1])],
        "channels": chan_list,
    }, separators=(",", ":"), ensure_ascii=False))
    (out / "guide.json").write_text(json.dumps({
        "built": built, "from": lo // 60, "to": hi // 60, "guide": guide,
    }, separators=(",", ":"), ensure_ascii=False))

    with_epg = sum(1 for c in chan_list if c["epg"])
    log(f"done: {len(chan_list)} channels, {with_epg} with guide data -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
