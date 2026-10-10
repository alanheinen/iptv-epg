# Signal TV Guide

A browser TV guide and player for the free US channels listed in
[iptv-org/iptv](https://github.com/iptv-org/iptv).

- Every `streams/us*.m3u` playlist (broadcast, local, Pluto TV, Samsung TV Plus,
  Plex, Roku, Tubi, Xumo, STIRR, PBS and the rest), merged into one channel list
- Channel names, categories and logos from the [iptv-org API](https://github.com/iptv-org/api)
- Programme listings from the public XMLTV feeds at [i.mjh.nz](https://i.mjh.nz/)
  (Pluto TV, Samsung TV Plus, Plex, Roku, PBS)
- Guide grid with a live now-line, channel search that also matches show titles,
  source and category filters, favorites, and an HLS player (hls.js) that falls
  back through a channel's alternate streams when one fails

## Run it locally

```bash
python3 scripts/build.py          # writes web/data/channels.json and guide.json (~10 s)
python3 scripts/serve.py          # http://127.0.0.1:8080
```

Needs only Python 3.9+ (standard library). Re-run the build every day or so;
the listings cover 3 hours back to 30 hours ahead.

`serve.py` serves the app plus a small stream proxy, and the page uses it
automatically when it's there. The proxy:

- starts a Pluto TV web session, because iptv-org's Pluto links are mostly
  refused ("channel not permitted for partner") and Pluto rejects other origins
- retries CORS-blocked streams server-side
- sends the Referer / User-Agent some streams need
- refuses private, loopback and link-local targets, so it can't reach your LAN

It listens on 127.0.0.1 only. Use `--host 0.0.0.0` to watch from other devices
on your network. Any static server works too (`python3 -m http.server -d web`),
but without the proxy far fewer channels play, and Pluto channels don't play at all.

## Container

```bash
docker build -t signal-tv .
docker run --rm -p 8080:8080 --read-only --tmpfs /data:uid=1000,gid=1000 signal-tv
```

The image runs `serve.py` as UID 1000 with a read-only root filesystem. It
builds the guide data into `/data` at startup (ready in about 10 s; `/healthz`
returns 503 until then) and rebuilds it every 4 hours. Give it about 512 MiB:
idle use is about 25 MiB, but the rebuild peaks near 250 MiB.

`.github/workflows/image.yml` builds, smoke-tests and Trivy-scans the image on
every push and PR, and on `main` pushes `ghcr.io/alanheinen/iptv-epg:sha-<commit>`.
The homelab deployment pins a digest in k8s-2025 under
`config/applications/signal-tv/`.

## Keys

| Key | Action |
| --- | --- |
| `[` / `]` | Previous / next channel |
| `/` | Search |
| `F` | Toggle favorite |
| `N` | Jump to now |
| `↑` `↓` `←` `→` `Enter` | Move around the guide, tune the selected channel |

## How listings are matched

`scripts/build.py` matches each stream to a schedule in this order:

1. Pluto TV channel ID in the stream URL (`plu-<id>`) → Pluto XMLTV ID
2. Call sign in the `tvg-id` feed (`PBS.us@KETS`) → PBS XMLTV ID
3. Normalised channel name → display names in the XMLTV feeds, trying the
   feed for the stream's own service first

Channels with no match still play; the guide shows "No listings" for them.

## Limits

Streams in iptv-org are third-party and unverified. Many are geo-blocked, offline,
or refuse browser playback (CORS), and some need custom HTTP headers a browser
can't send. When a channel won't play, the player says why and offers the stream
URL for VLC or mpv. NSFW channels are excluded.
