# Configuration

Signal has two programs. `scripts/serve.py` is the server, which every install
method runs. `scripts/build.py` makes the guide data, and the server runs it
for you when given `--refresh`. Neither has a config file of its own; settings
are command-line options, plus one environment variable.

## Server: `scripts/serve.py`

| Option | Default | Description |
| --- | --- | --- |
| `--host ADDR` | `127.0.0.1` | Address to listen on. `0.0.0.0` listens on every interface. |
| `--port N` | `8080` | Port to listen on. |
| `--data DIR` | `web/data` | Directory for `channels.json`, `guide.json` and `feed-cache.json`. Created if missing. |
| `--refresh HOURS` | `0` | Rebuild the guide at startup and then every `HOURS` (fractions are fine). `0` never rebuilds; the server then serves whatever is in `--data`. |
| `--countries LIST` | `$COUNTRIES`, else `us` | Countries for the rebuilds. Sets `COUNTRIES` for the build. |
| `--log FILE` | console | Append all output, including the build's, to `FILE`. At startup a log over ~5 MB is moved to `FILE.1`. |

A failed rebuild is retried after 10 minutes. The previous guide keeps being
served in the meantime.

The server logs page requests and every failed proxy request (with the
upstream URL). Successful stream fetches aren't logged, since there are
several a second while someone watches.

### Endpoints

| Path | Purpose |
| --- | --- |
| `/` | The page (`web/`) |
| `/data/channels.json`, `/data/guide.json` | Guide data, served from `--data` with `Cache-Control: no-cache` |
| `/healthz` | `200` once `channels.json` exists, `503` before. Use it for readiness checks. |
| `/api/health` | Always `200` while the server runs. The page uses it to detect the proxy. |
| `/proxy?u=URL[&r=REFERER][&a=USER_AGENT]` | Fetches `URL` for the browser. See [how the proxy works](how-it-works.md#the-stream-proxy). |
| `/pluto/<channel-id>.m3u8` | A Pluto TV channel through its own Pluto session |

## Guide build: `scripts/build.py`

| Option | Default | Description |
| --- | --- | --- |
| `--countries LIST` | `$COUNTRIES`, else `us` | Countries to build, comma-separated, as iptv-org playlist prefixes. |
| `--out DIR` | `web/data` | Where to write the data. |
| `--no-epg` | off | Skip the listings feeds: channels only, built in seconds. |

The build writes `channels.json` and `guide.json` under temporary names and
renames them into place. A server reading the directory never sees half a
file.

Other settings are constants at the top of `scripts/build.py`:

| Constant | Value | Meaning |
| --- | --- | --- |
| `WINDOW_BEFORE_H` / `WINDOW_AFTER_H` | 3 / 30 | Hours of listings kept before and after now |
| `CACHE_AHEAD_H` | 96 | Hours ahead kept from the cached (epgshare01) feeds |
| `DESC_MAX` | 320 | Programme descriptions are cut to this many characters |

## Choosing countries

Countries are the prefixes of iptv-org's playlist files: `us` covers `us.m3u`,
`us_local.m3u`, `us_pluto.m3u` and the rest. They're mostly ISO country codes,
but the UK is `uk`. The full list is the
[`streams/` directory](https://github.com/iptv-org/iptv/tree/master/streams).

- The first country listed owns a channel that appears in several. That
  decides which source it's filed under.
- Listings are only matched within a channel's own country, except Pluto
  channel ids, which identify the same channel in any region's feed.
- More countries means a longer build and more memory: `us,ca,uk` peaks about
  25 MiB higher than `us` alone.

### Where each install method keeps it

| Method | Where | Apply with |
| --- | --- | --- |
| Docker Compose | `COUNTRIES:` in `docker-compose.yml` | `docker compose up -d` |
| `docker run` | `-e COUNTRIES=us,ca,uk` | recreate the container |
| Kubernetes | the `COUNTRIES` env in `deploy/kubernetes/deployment.yaml` | `kubectl apply -k deploy/kubernetes` |
| Linux | `COUNTRIES=` in `/etc/signal-tv.env` | `sudo systemctl restart signal-tv` |
| Windows | `install.ps1 -Countries us,ca,uk` | runs it for you |

The Linux env file also holds `HOST` and `PORT`. On Windows, `-Port` and
`-Lan` (listen on every interface) are parameters of `install.ps1`.

## Adding a country

A country without its own entry still gets i.mjh.nz listings for its Pluto TV
and Samsung TV Plus playlists, when it has them. To give it more, add it to
`COUNTRY_FEEDS` in `scripts/build.py`:

```python
COUNTRY_FEEDS = {
    ...
    "au": {
        "samsung": (MJH + "SamsungTVPlus/au.xml.gz", ["au_samsung"]),
        "plex": (MJH + "Plex/au.xml.gz", []),
        "cable": (EPGSHARE + "AU1.xml.gz", ["au"]),
    },
}
```

Each entry is `kind: (feed URL, playlists matched against it first)`.

- **Kinds:** `pluto`, `samsung`, `plex`, `roku`, `pbs`, `cable` and `locals`.
  `cable` and `locals` are cached between builds. A channel that doesn't match
  its own service's feed is then tried against the country's `cable`, Samsung,
  Plex, Roku and Pluto feeds, in that order.
- **Feeds available:**
  - i.mjh.nz lists its feeds by region at <https://i.mjh.nz/>. It names the UK
    `gb`; `MJH_REGION` maps such names.
  - epgshare01's country feeds are named `epg_ripper_<CC><n>.xml.gz` and listed
    at <https://epgshare01.online/epgshare01/>.
- **Checking it:** build with `--countries <cc>` and compare the
  `with guide data` count in the build's last line before and after.

## Fixing a channel by hand

Some channels can't be matched by name because the feeds spell them too
differently. For example, the UK feed calls BBC One East Midlands "BBC One E Mid HD". These go in
`LISTING_TABLE` in `scripts/build.py`, keyed by the channel's iptv-org
`tvg-id` (with any `HD` dropped from the part after `@`):

```python
"BBCOne.uk@EastMidlands": ("cable-uk", "BBC.One.E.Mid.HD.uk"),
```

The value is the feed key (`<kind>-<country>`) and the channel's id in that
XMLTV feed. Only map a channel to its own schedule. A neighbouring station's
listings look plausible but are wrong, which is worse than none.
