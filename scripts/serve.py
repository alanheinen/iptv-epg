#!/usr/bin/env python3
"""Serve the guide with a small HLS proxy so more streams play in a browser.

    python3 scripts/serve.py [--host 127.0.0.1] [--port 8080]

Besides the static files in web/, it answers:

  /api/health              lets the page know the proxy is available
  /proxy?u=URL[&r=REF][&a=UA]
                           fetches URL server-side (optionally with Referer and
                           User-Agent), rewrites HLS playlists so every child
                           playlist and segment also goes through the proxy,
                           and adds CORS headers
  /pluto/<channel>.m3u8    starts a Pluto TV web session and returns that
                           channel's playlist (iptv-org's Pluto links are
                           mostly refused, and Pluto blocks other origins)

Requests to private, loopback and link-local addresses are refused so the
proxy can't be used to reach your own network. It binds to 127.0.0.1 unless
you pass --host. Standard library only.

With --data DIR the guide data is served from DIR instead of web/data, and
with --refresh HOURS the server rebuilds it (scripts/build.py) at startup and
on that interval, which is how every deployment keeps its listings current.
--countries picks the countries for those rebuilds (or set COUNTRIES), and
--log FILE sends all output to a file, for running without a console.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
BUILD = Path(__file__).resolve().parent / "build.py"
DATA = WEB / "data"
DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 20
CHUNK = 64 * 1024


class Refused(Exception):
    pass


def check_host(url: str) -> None:
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise Refused("only http and https URLs can be proxied")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as e:
        raise Refused(f"can't resolve {p.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise Refused(f"{p.hostname} resolves to a private address")


class NoPrivateRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(NoPrivateRedirects)


def upstream(url: str, ref: str = "", ua: str = "", rng: str = ""):
    check_host(url)
    h = {"User-Agent": ua or DEFAULT_UA, "Accept": "*/*"}
    if ref:
        h["Referer"] = ref
        h["Origin"] = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(ref))
    if rng:
        h["Range"] = rng
    return OPENER.open(urllib.request.Request(url, headers=h), timeout=TIMEOUT)


def proxied(url: str, ref: str, ua: str) -> str:
    q = {"u": url}
    if ref:
        q["r"] = ref
    if ua:
        q["a"] = ua
    return "/proxy?" + urllib.parse.urlencode(q)


URI_ATTR = re.compile(r'URI="([^"]+)"')


def rewrite_playlist(text: str, base: str, ref: str, ua: str) -> str:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
        elif s.startswith("#"):
            out.append(URI_ATTR.sub(lambda m: f'URI="{proxied(urllib.parse.urljoin(base, m.group(1)), ref, ua)}"', line))
        else:
            out.append(proxied(urllib.parse.urljoin(base, s), ref, ua))
    return "\n".join(out) + "\n"


def is_playlist(head: bytes) -> bool:
    # Sniff, never trust the Content-Type or extension: Pluto serves its
    # 16-byte AES-128 keys as application/vnd.apple.mpegurl, and rewriting one
    # as a playlist breaks decryption of every segment.
    return head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"#EXTM3U")


# ---------------------------------------------------------------- pluto

class Pluto:
    """Pluto TV web sessions, one per channel. A session plays one channel at
    a time: starting a second channel on it empties the first one's playlists
    within a few seconds, so two viewers on different channels (or a TV being
    cast to while the browser surfs) would knock each other off."""

    BOOT = "https://boot.pluto.tv/v4/start"
    MAX = 64

    def __init__(self):
        self.lock = threading.Lock()
        self.sessions: dict[str, tuple[dict, float]] = {}

    def start(self, channel: str) -> dict:
        with self.lock:
            hit = self.sessions.get(channel)
            if hit and time.time() < hit[1]:
                return hit[0]
        q = urllib.parse.urlencode({
            "appName": "web", "appVersion": "9.0.0", "deviceVersion": "124.0.0",
            "deviceModel": "web", "deviceMake": "chrome", "deviceType": "web",
            "clientID": str(uuid.uuid4()), "clientModelNumber": "1.0.0", "serverSideAds": "false",
            "channelID": channel,
        })
        with upstream(f"{self.BOOT}?{q}") as r:
            d = json.load(r)
        session = {
            "stitcher": d["servers"]["stitcher"].rstrip("/"),
            "params": d.get("stitcherParams", ""),
            "token": d["sessionToken"],
        }
        expires = time.time() + min(int(d.get("refreshInSec", 3600)), 4 * 3600) - 60
        with self.lock:
            now = time.time()
            for k in [k for k, (_, exp) in self.sessions.items() if exp < now]:
                del self.sessions[k]
            while len(self.sessions) >= self.MAX:
                del self.sessions[min(self.sessions, key=lambda k: self.sessions[k][1])]
            self.sessions[channel] = (session, expires)
        return session

    def forget(self, channel: str) -> None:
        with self.lock:
            self.sessions.pop(channel, None)

    def master_url(self, channel: str) -> str:
        s = self.start(channel)
        params = (s["params"] + "&" if s["params"] else "") + urllib.parse.urlencode(
            {"jwt": s["token"], "masterJWTPassthrough": "true"})
        return f"{s['stitcher']}/v2/stitch/hls/channel/{channel}/master.m3u8?{params}"


PLUTO = Pluto()


# ---------------------------------------------------------------- handler

class Handler(SimpleHTTPRequestHandler):
    server_version = "SignalGuide/1.0"
    # Fixed types: on Windows, mimetypes reads the registry, which can map
    # .js to text/plain and break the page.
    extensions_map = {
        "": "application/octet-stream", ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
    }

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def translate_path(self, path):
        clean = urllib.parse.unquote(path.split("?", 1)[0])
        if clean.startswith("/data/"):
            name = clean[len("/data/"):]
            if name in ("channels.json", "guide.json"):
                return str(DATA / name)
        return super().translate_path(path)

    def log_request(self, code="-", size="-"):
        # Segment fetches are constant noise; keep only the proxy's failures,
        # which log the upstream reason.
        if self.path.startswith("/proxy") and str(getattr(code, "value", code)).startswith(("2", "3")):
            return
        super().log_request(code, size)

    def log_error(self, fmt, *args):
        target = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("u", [""])[0]
        super().log_error(fmt + (" [%s]" % target[:200] if target else ""), *args)

    def end_headers(self):
        if self.path.startswith(("/proxy", "/pluto/", "/api/")):
            self.send_header("Access-Control-Allow-Origin", "*")
        if self.path.startswith("/data/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        path, _, query = self.path.partition("?")
        try:
            if path == "/api/health":
                return self.send_json({"proxy": True, "pluto": True})
            if path == "/healthz":
                ok = (DATA / "channels.json").exists()
                return self.send_json({"ready": ok}) if ok else self.send_error(503, "guide data not built yet")
            if path == "/proxy":
                q = urllib.parse.parse_qs(query)
                return self.relay(q.get("u", [""])[0], q.get("r", [""])[0], q.get("a", [""])[0])
            m = re.fullmatch(r"/pluto/([0-9a-f]{24})\.m3u8", path)
            if m:
                try:
                    return self.relay(PLUTO.master_url(m.group(1)), "", "")
                except urllib.error.HTTPError as e:
                    if e.code in (401, 403):  # session went stale; start a new one
                        PLUTO.forget(m.group(1))
                        return self.relay(PLUTO.master_url(m.group(1)), "", "")
                    raise
        except Refused as e:
            return self.send_error(403, str(e))
        except urllib.error.HTTPError as e:
            return self.send_error(e.code if 400 <= e.code < 600 else 502, f"upstream: {e.reason}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return self.send_error(502, f"upstream: {getattr(e, 'reason', e)}")
        return super().do_GET()

    def send_json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def relay(self, url: str, ref: str, ua: str):
        with upstream(url, ref, ua, self.headers.get("Range", "")) as r:
            ctype = r.headers.get("Content-Type", "application/octet-stream")
            final = r.geturl()
            head = r.read(CHUNK)
            if is_playlist(head):
                text = (head + r.read()).decode("utf-8", "replace")
                body = rewrite_playlist(text, final, ref, ua).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(r.status)
            self.send_header("Content-Type", ctype)
            for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
                if r.headers.get(h):
                    self.send_header(h, r.headers[h])
            self.end_headers()
            try:
                self.wfile.write(head)
                while chunk := r.read(CHUNK):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


def refresher(hours: float) -> None:
    """Rebuild the guide data now and then every `hours`, in a child process
    so the parser's memory is returned after each run."""
    while True:
        started = time.time()
        # The build logs to stderr; with --log that's the log file.
        r = subprocess.run([sys.executable, str(BUILD), "--out", str(DATA)], stdout=sys.stderr, stderr=sys.stderr)
        if r.returncode != 0:
            print(f"guide refresh failed (exit {r.returncode}); retrying in 10 minutes", flush=True)
            time.sleep(600)
            continue
        time.sleep(max(60.0, hours * 3600 - (time.time() - started)))


def main():
    global DATA
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data", help="directory holding channels.json and guide.json (default web/data)")
    ap.add_argument("--refresh", type=float, default=0, help="rebuild the guide data every N hours (0 = never)")
    ap.add_argument("--countries", help="countries for the rebuild, e.g. us,ca,uk (default: $COUNTRIES or us)")
    ap.add_argument("--log", help="append output to this file instead of the console (kept under ~5 MB)")
    a = ap.parse_args()
    if a.log:
        # Needed under pythonw.exe (the Windows background task), which has no
        # console: without it, sys.stderr is None and logging a request fails.
        log = Path(a.log)
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists() and log.stat().st_size > 5_000_000:
            log.replace(log.with_name(log.name + ".1"))
        sys.stdout = sys.stderr = open(log, "a", encoding="utf-8", errors="replace", buffering=1)
    if a.countries:
        os.environ["COUNTRIES"] = a.countries
    if a.data:
        DATA = Path(a.data)
        DATA.mkdir(parents=True, exist_ok=True)
    if a.refresh > 0:
        threading.Thread(target=refresher, args=(a.refresh,), daemon=True).start()
    elif not (DATA / "channels.json").exists():
        print(f"{DATA}/channels.json is missing. Run: python3 scripts/build.py", flush=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"Signal TV Guide on http://{a.host if a.host != '0.0.0.0' else 'localhost'}:{a.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
