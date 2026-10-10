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
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
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


def is_playlist(url: str, ctype: str, head: bytes) -> bool:
    return ("mpegurl" in ctype.lower() or urllib.parse.urlsplit(url).path.endswith(".m3u8")
            or head.lstrip().startswith(b"#EXTM3U"))


# ---------------------------------------------------------------- pluto

class Pluto:
    BOOT = "https://boot.pluto.tv/v4/start"

    def __init__(self):
        self.lock = threading.Lock()
        self.session = None
        self.expires = 0.0
        self.client = str(uuid.uuid4())

    def start(self) -> dict:
        with self.lock:
            if self.session and time.time() < self.expires:
                return self.session
            q = urllib.parse.urlencode({
                "appName": "web", "appVersion": "9.0.0", "deviceVersion": "124.0.0",
                "deviceModel": "web", "deviceMake": "chrome", "deviceType": "web",
                "clientID": self.client, "clientModelNumber": "1.0.0", "serverSideAds": "false",
            })
            with upstream(f"{self.BOOT}?{q}") as r:
                d = json.load(r)
            self.session = {
                "stitcher": d["servers"]["stitcher"].rstrip("/"),
                "params": d.get("stitcherParams", ""),
                "token": d["sessionToken"],
            }
            self.expires = time.time() + min(int(d.get("refreshInSec", 3600)), 4 * 3600) - 60
            return self.session

    def master_url(self, channel: str) -> str:
        s = self.start()
        params = (s["params"] + "&" if s["params"] else "") + urllib.parse.urlencode(
            {"jwt": s["token"], "masterJWTPassthrough": "true"})
        return f"{s['stitcher']}/v2/stitch/hls/channel/{channel}/master.m3u8?{params}"


PLUTO = Pluto()


# ---------------------------------------------------------------- handler

class Handler(SimpleHTTPRequestHandler):
    server_version = "SignalGuide/1.0"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, fmt, *args):
        if not self.path.startswith("/proxy"):
            super().log_message(fmt, *args)

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
            if path == "/proxy":
                q = urllib.parse.parse_qs(query)
                return self.relay(q.get("u", [""])[0], q.get("r", [""])[0], q.get("a", [""])[0])
            m = re.fullmatch(r"/pluto/([0-9a-f]{24})\.m3u8", path)
            if m:
                try:
                    return self.relay(PLUTO.master_url(m.group(1)), "", "")
                except urllib.error.HTTPError as e:
                    if e.code in (401, 403):  # session went stale; start a new one
                        PLUTO.session = None
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
            if is_playlist(final, ctype, head):
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    if not (WEB / "data" / "channels.json").exists():
        print("web/data/channels.json is missing. Run: python3 scripts/build.py")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"Signal TV Guide on http://{a.host if a.host != '0.0.0.0' else 'localhost'}:{a.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
