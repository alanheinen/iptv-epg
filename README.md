# Signal TV Guide

A browser TV guide and player for the free channels listed in
[iptv-org/iptv](https://github.com/iptv-org/iptv), US by default.

- Every playlist for the chosen countries (for the US: broadcast, local,
  Pluto TV, Samsung TV Plus, Plex, Roku, Tubi, Xumo, STIRR, PBS and the rest),
  merged into one channel list. See [Countries](#countries)
- Channel names, categories, logos and languages from the [iptv-org API](https://github.com/iptv-org/api)
- Programme listings from public XMLTV feeds: [i.mjh.nz](https://i.mjh.nz/)
  for Pluto TV, Samsung TV Plus, Plex, Roku and PBS, and
  [epgshare01](https://epgshare01.online/) for cable networks (sports ones included) and
  local broadcast stations
- Guide grid with a live now-line, channel search that also matches show titles,
  country, source, language and category filters, favorites, and an HLS player (hls.js) that falls
  back through a channel's alternate streams when one fails
- Google Cast (Chromecast, Google TV, Nest Hub) from Chrome or Edge, when the
  proxy is running. Streams that already have HTTPS and CORS go to the TV
  directly; the rest, and Pluto, go through the proxy, so the TV must be able
  to resolve and reach the server's hostname. The cast button stays visible
  (dimmed) when Chrome has found no devices; clicking it opens Chrome's Cast
  picker, which searches again
- A copy button on every channel row for the stream URL, to open it in VLC or
  mpv. Pluto channels and streams that need special headers copy as a link
  through the proxy, since their raw URLs don't play on their own

## Install

Every method runs the same thing: `scripts/serve.py`, which serves the page,
runs the stream proxy, and rebuilds the guide data at startup and every hour.
The first build takes about a minute. Each one listens on this machine only
until you change one setting, and each takes the countries to build
(`us` by default; see [Countries](#countries)).

**Keep it off the internet.** Serve it on your own network at most: exposed
publicly, the stream proxy is an open relay for anyone's traffic.

| | Needs | Opens it to your network |
| --- | --- | --- |
| [Docker](#docker) | Docker with Compose | `ports: "8080:8080"` in `docker-compose.yml` |
| [Kubernetes](#kubernetes) | a cluster, `kubectl` | an Ingress or Service of your choice |
| [Linux](#linux-systemd) | Python 3.9+, systemd | `HOST=0.0.0.0` in `/etc/signal-tv.env` |
| [Windows](#windows) | Python 3.9+ from python.org | `-Lan` (administrator PowerShell) |

Memory: about 25 MiB idle; a rebuild peaks near 150 MiB for the US and
180 MiB for us,ca,uk. Allow 256 to 512 MiB.

### Docker

```bash
docker compose up -d        # http://localhost:8080; docker compose ps shows "healthy" when ready
```

[`docker-compose.yml`](docker-compose.yml) uses `ghcr.io/alanheinen/iptv-epg:latest`,
or builds it from the checkout if it can't pull (`--build` always builds).
Set `COUNTRIES` there. It runs read-only as UID 1000 with no capabilities, and
keeps the feed cache in a named volume, so a restart is ready in seconds.
Update with `docker compose pull && docker compose up -d`.

Without Compose:

```bash
docker run -d --name signal-tv --restart unless-stopped -p 127.0.0.1:8080:8080 \
  -e COUNTRIES=us --read-only --cap-drop ALL -v signal-tv-data:/data \
  ghcr.io/alanheinen/iptv-epg:latest
```

### Kubernetes

```bash
kubectl apply -k deploy/kubernetes
kubectl -n signal-tv port-forward svc/signal-tv 8080:80     # http://localhost:8080
```

[`deploy/kubernetes`](deploy/kubernetes) is a Kustomize base: a `signal-tv`
namespace (Pod Security "restricted"), a one-replica Deployment and a Service.
Set `COUNTRIES` in `deployment.yaml` and pin the image in
`kustomization.yaml` (`newTag: sha-<commit>` or a digest) rather than following
`latest`. `ingress.yaml` is a starting point for reaching it from your
network; add it to the resources once its host, class and TLS fit your
cluster. Casting needs the page on HTTPS.

### Linux (systemd)

```bash
sudo deploy/linux/install.sh                 # http://127.0.0.1:8080
sudo deploy/linux/install.sh --uninstall
```

The script copies the app to `/opt/signal-tv` and installs
[`signal-tv.service`](deploy/linux/signal-tv.service), which runs as a
throwaway system user (`DynamicUser`) in a tight sandbox, keeps its data in
`/var/lib/signal-tv`, and restarts on failure. Settings (`COUNTRIES`, `HOST`,
`PORT`) are in `/etc/signal-tv.env`, written on first install and kept after
that; `sudo systemctl restart signal-tv` after editing it. Logs:
`journalctl -u signal-tv`. To update, pull the repository and run the script
again.

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Countries us
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Uninstall
```

[`install.ps1`](deploy/windows/install.ps1) copies the app to
`%LOCALAPPDATA%\SignalTV` and registers a scheduled task, "Signal TV Guide",
that runs it with `pythonw.exe` (no console window) whenever you log on, and
starts it now. No administrator rights needed, except for `-Lan`, which
listens on every interface and adds a Windows Firewall rule for Private
networks only. Logs are in `%LOCALAPPDATA%\SignalTV\signal-tv.log`. Run it
again to update or to change `-Countries`, `-Port` or `-Lan`; `-WhatIf` shows
what it would do.

### From a checkout

```bash
python3 scripts/build.py          # writes web/data/channels.json and guide.json (~30 s)
python3 scripts/serve.py          # http://127.0.0.1:8080
```

Or `python3 scripts/serve.py --data DIR --refresh 1` to rebuild hourly as the
services do. Needs only Python 3.9+ (standard library). The listings cover 3 hours back to
30 hours ahead, but some feeds run short (Samsung TV Plus is often only 4 to
10 hours ahead), so re-run the build every hour or two. An open page picks up
new data by itself within 10 minutes.

The epgshare01 feeds change about once a day and the locals feed is ~60 MB, so
the build keeps what it needs from them in `feed-cache.json` and re-downloads
only when they change (`If-Modified-Since`). A new iptv-org channel can only
match a cached feed after its next change upstream.

`serve.py` serves the app plus a small stream proxy, and the page uses it
automatically when it's there. The proxy:

- starts a Pluto TV web session per channel, because iptv-org's Pluto links
  are mostly refused ("channel not permitted for partner") and Pluto rejects
  other origins. A Pluto session plays one channel at a time, so sharing one
  would let two viewers on different channels knock each other off
- retries CORS-blocked streams server-side
- sends the Referer / User-Agent some streams need
- refuses private, loopback and link-local targets, so it can't reach your LAN
- tells playlists from other files by their content (`#EXTM3U`), never the
  Content-Type: Pluto labels its AES keys as playlists

In the browser it's a fallback: a stream is tried directly first and goes
through the proxy only when it has to (Pluto, plain HTTP on an HTTPS page,
required headers, or a failed direct attempt). Casting follows the same rule.
Proxy failures are logged with the upstream URL; successful fetches aren't.

It listens on 127.0.0.1 only. Use `--host 0.0.0.0` to watch from other devices
on your network. Any static server works too (`python3 -m http.server -d web`),
but without the proxy far fewer channels play, and Pluto channels don't play at all.

## Countries

```bash
python3 scripts/build.py --countries us,ca,uk     # or COUNTRIES=us,ca,uk
```

Countries are iptv-org playlist prefixes, so the UK is `uk`. The first one
listed owns a channel that appears in several. The page shows a country filter
when there's more than one, and its source and language lists follow the
chosen country. Language comes from iptv-org's feed data; channels without a
`tvg-id` (many Pluto and Samsung ones) show under "Not tagged".

Listings are matched within each channel's own country, and how many a country
gets depends on the feeds behind it:

| Country | Feeds | Channels | With listings |
| --- | --- | --- | --- |
| `us` | Pluto, Samsung, Plex, Roku, PBS, epgshare01 cable and locals | 2,239 | 1,049 |
| `ca` | Pluto, Samsung, Plex, epgshare01 CA2 | 341 | 233 |
| `uk` | Pluto, Samsung, Plex, epgshare01 UK1 | 507 | 359 |

Other countries work too, with the i.mjh.nz Pluto and Samsung feeds for
whichever of those playlists they have; add an entry to `COUNTRY_FEEDS` in
`scripts/build.py` for more. Channels the feeds name too differently to match
(BBC One's regions, "BBC One E Mid HD"; CBC stations, which the Canadian feed
names by city) are mapped by hand in `LISTING_TABLE`. CBC New Brunswick and
North, and BBC Four/CBeebies and BBC Three/CBBC, have no listings: the feed has
no matching station, or the channel switches between two schedules.

## Image

`.github/workflows/image.yml` builds, smoke-tests and Trivy-scans the image on
every push and PR, and on `main` pushes `ghcr.io/alanheinen/iptv-epg:sha-<commit>`
and `:latest`. The build reads only the parts of each feed and iptv-org API
file that the playlists could use, which keeps its memory low. The homelab
deployment pins a digest in k8s-2025 under `config/applications/signal-tv/`.

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

Everything except step 1 stays within the stream's own country.

1. Pluto TV channel ID in the stream URL (`plu-<id>`) → Pluto XMLTV ID, in its
   own country's feed first, then any other built country's
2. `tvg-id` in `LISTING_TABLE` (BBC One regions, CBC stations) → that XMLTV ID,
   then the call sign in the `tvg-id` feed (`PBS.us@KETS`) → PBS XMLTV ID
3. Normalised channel name → the feed for the stream's own service
4. Station call sign (`ABC.us@KRGVTV`, `CBS 9 Oklahoma City OK (KWTV-DT1)`)
   → the epgshare01 locals feed, for the broadcast, local and PBS playlists only
5. Normalised channel name → cable, then the FAST service feeds of the
   stream's country
6. iptv-org's own guide mappings (`guides.json`) that point at a feed the
   build already downloads (i.mjh.nz, plex.tv)

Step 4 is deliberately strict, because a wrong schedule is worse than none.
The locals feed has each station's main channel only, so subchannels
(`-DT2`, `57.2`) aren't matched. The FAST services' station-branded channels
are mostly news or spin-offs ("WBTV Nikita"), not the broadcast. When several
channels land on one station, only one plainly named after it keeps the match.

Channels with no match still play; the guide shows "No listings" for them.
Most of what's left is community access, foreign, music and niche FAST
channels that no public XMLTV feed covers.

## Limits

Streams in iptv-org are third-party and unverified. Many are geo-blocked, offline,
or refuse browser playback (CORS), and some need custom HTTP headers a browser
can't send. When a channel won't play, the player says why and offers the stream
URL for VLC or mpv. NSFW channels are excluded.
