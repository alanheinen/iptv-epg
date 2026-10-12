# How it works

```
iptv-org playlists ──┐
iptv-org API ────────┼──► build.py ──► channels.json ──┐
XMLTV listings feeds ┘     (hourly)    guide.json      ├──► serve.py ──► browser
                                                       │    (page, data,
                     stream hosts ◄── stream proxy ◄───┘     proxy, Pluto)
```

There are three parts:

- **`scripts/build.py`** makes the guide data. It's a separate process, so its
  memory is returned after every run.
- **`scripts/serve.py`** serves the page and the data, runs the stream proxy,
  and starts a build at startup and every hour.
- **`web/`** is the page itself: static HTML, CSS and JavaScript, with hls.js
  loaded from a CDN.

## Building the guide

Each build:

1. **Playlists.** Lists iptv-org's `streams/` directory through the GitHub API
   and downloads every playlist for the chosen countries (`us.m3u`,
   `us_pluto.m3u`...). Each `#EXTINF` line gives a name, a `tvg-id` and
   quality and status tags; `#EXTVLCOPT` lines give a required Referer or
   User-Agent.
2. **Metadata.** Downloads iptv-org's `channels`, `feeds`, `logos`,
   `categories`, `languages`, `countries` and `guides` files. They cover every
   channel in the world (`guides.json` alone is ~25 MB), so they're read one
   record at a time and only the playlists' channels are kept.
3. **Listings.** Downloads the XMLTV feeds for each country, four at a time,
   and parses them as they stream in. A feed's channels come before its
   programmes, so each channel is checked as it arrives against everything
   a match below could look for. Programmes are kept only for channels that
   pass, which halves the build's memory.
4. **Matching.** Groups the streams into channels by `tvg-id` (or by name for
   untagged streams) and gives each channel a schedule. See below.
5. **Output.** Writes `channels.json` and `guide.json`, and the feed cache.

### The feed cache

The epgshare01 feeds change about once a day and the US locals feed is
~60 MB, so they're requested with `If-Modified-Since`. When unchanged, the build reuses what it
kept last time in `feed-cache.json`: the names and programmes of the channels
it matched, 96 hours ahead. A failed download also falls back to the cache.

A channel new to iptv-org can only match a cached feed after that feed's next
change upstream, normally within a day. The i.mjh.nz feeds are small and
downloaded every time.

### Matching channels to listings

Every rule except the first stays within the stream's own country. In order:

1. **Pluto TV channel id** in the stream URL (`plu-<id>`, `/channel/<id>/`).
   The id is looked up in the country's Pluto feed first, then any other
   built country's, since a Pluto id is the same channel in any region.
2. **`LISTING_TABLE`**, a short hand-made map of `tvg-id` to XMLTV id. It
   exists for channels the feeds name too differently to match: BBC One's
   regions ("BBC One E Mid HD") and CBC's stations, which the Canadian feed
   names by city.
3. **PBS call sign** in the `tvg-id` feed (`PBS.us@KETS`), against the PBS
   feed.
4. **Channel name**, normalised, against the feed for the stream's own
   service (a Samsung TV Plus stream against the Samsung feed).
   - Normalising lowercases the name, drops accents, brackets, "HD", "East"
     and punctuation, and spells out `&` and `+`.
5. **Station call sign**, from the `tvg-id` feed (`ABC.us@KRGVTV`) or the
   name (`CBS 9 Oklahoma City OK (KWTV-DT1)`), against epgshare01's US locals
   feed. Broadcast and local playlists only.
6. **Channel name** (or the name in the `tvg-id`) against the country's
   other feeds:
   - in this order: cable, Samsung, Plex, Roku, then Pluto
   - East-coast versions of a channel win over Pacific/West ones.
7. **iptv-org's own guide mappings** (`guides.json`), when they point at a
   feed the build already downloads (i.mjh.nz or plex.tv ids).

Rule 5 is deliberately strict, because a wrong schedule is worse than none:

- **Main channels only.** The locals feed lists each station's main channel,
  so subchannels (`-DT2`, `57.2`) never match.
- **No streaming-service copies.** Free streaming services carry
  station-branded channels that aren't the broadcast: news loops and spin-offs
  like "WBTV Nikita". Their `tvg-id` feeds are often wrong too, so those
  services never match a station.
- **One channel per station.** When several channels land on one station,
  only one plainly named after it (`WSOC-DT1`, `KTTV 11.1`) keeps the match.

A channel with no match still plays; the guide shows "No listings" for it.

### Channel order and streams

Channels with listings come first, then the rest, each group alphabetical.
They're numbered from 101 in that order, so numbers shift as iptv-org
changes. A channel's streams put flagged ones (`[Geo-blocked]`, `[Not 24/7]`)
last; otherwise HTTPS comes first, then the highest resolution. NSFW channels
are dropped.

## Data files

Both files are compact JSON, rebuilt every hour.

### `channels.json`

```jsonc
{
  "built": "2026-10-11T23:17:31+00:00",
  "sources":   [{ "id": "us_pluto", "label": "Pluto TV", "cc": "us", "count": 344 }],
  "countries": [{ "id": "us", "name": "United States", "flag": "🇺🇸", "count": 2239 }],
  "languages": [{ "id": "eng", "name": "English", "count": 1814 }],
  "channels": [{
    "id": "Tastemade.us@US",          // tvg-id; untagged: "n:<name>" (US), "n:<cc>:<name>"
    "name": "Tastemade",
    "logo": "https://…",
    "cats": ["Cooking"],              // iptv-org categories
    "network": "", "site": "https://…",
    "src": "us_cineversetv",          // the playlist it came from first
    "cc": ["us"],                     // every country listing it
    "langs": ["eng"],                 // ISO 639-3; empty when iptv-org has none
    "streams": [{ "url": "https://…m3u8", "q": "1080p", "ref": "…", "ua": "…", "tags": ["Geo-blocked"] }],
    "epg": "pluto-us:5ce4475cd43850831ca91ce7"   // feed key and XMLTV id, or null
  }]
}
```

Optional stream fields (`q`, `ref`, `ua`, `tags`) are left out when empty.

### `guide.json`

```jsonc
{
  "built": "…",
  "from": 29342580, "to": 29344560,   // window, in minutes since the Unix epoch
  "guide": {
    "Tastemade.us@US": [
      [29343570, 29343600, "Pati's Mexican Table", "Yucatán Meats", "Temozón is…", "S12 E6", "Series"]
    ]
  }
}
```

Each programme is `[start, stop, title, subtitle, description, episode,
category]`, with times in minutes since the epoch. Trailing empty fields are
dropped, so most programmes are shorter.

## The page

The page loads both files, lays out a virtualised grid (only the visible rows
are drawn), and keeps it live:

- **Now line:** moves every 30 seconds, along with the past/live styling.
- **New data:** checked every 10 minutes and whenever the tab comes back into
  view. A new build is swapped in place, keeping your filters, scroll position,
  selection and playback.
- **Remembered in `localStorage`:** favorites, filters, theme and the last
  channel.

### Playing a stream

For each stream, the page picks a route:

- **Direct**, when the browser can fetch the stream itself.
- **The proxy**, when it can't:
  - Pluto channels
  - plain-HTTP streams on an HTTPS page
  - streams that need a Referer or User-Agent
  - a direct attempt that failed (often CORS)

Each route has 15 seconds to start playing. After that, or on a fatal error,
the page tries the next route, then the channel's next stream. When nothing
plays it says why and offers the URLs. Without the server's proxy (any static
web server works), only direct routes are tried.

## The stream proxy

`/proxy?u=URL` fetches `URL` server-side and returns it with
`Access-Control-Allow-Origin: *`.

- **Playlists:** HLS playlists are rewritten so every child playlist,
  segment and key URL also goes through the proxy. The proxy recognises a
  playlist by its content (`#EXTM3U`), never its Content-Type: Pluto labels
  its AES-128 keys as playlists, and rewriting a key breaks decryption.
- **Headers:** `r=` and `a=` add a Referer (with a matching Origin) and a
  User-Agent. Range requests pass through.
- **Refused targets:** anything that resolves to a private, loopback,
  link-local, reserved or multicast address, including after redirects.
  The proxy can't be used to reach your network or the host's services.

The proxy holds nothing between requests and keeps no logs of what's
watched; failed fetches are logged with their URL, for troubleshooting.

### Pluto TV

iptv-org's Pluto links are mostly refused ("channel not permitted for
partner"), and Pluto rejects other sites' pages. For `/pluto/<id>.m3u8` the
server instead starts a Pluto web session the way pluto.tv does, and returns
the channel's stitched playlist through the proxy.

A Pluto session plays one channel at a time. Starting a second channel on it
empties the first one's playlists within seconds. So the server keeps one
session per channel, so two viewers or a TV can watch different channels:

- Sessions are reused until shortly before they expire (at most 4 hours).
- A stale session (401/403) is replaced and retried once.
- At most 64 sessions are kept.

## Casting

In Chrome and Edge, the page loads Google's Cast sender library and offers
Chromecast, Google TV and Nest Hub devices.

The TV runs Google's Default Media Receiver and fetches the stream itself, so:

- **Direct, when possible.** A stream with HTTPS and CORS is sent to the TV
  directly. The page checks this by fetching the playlist itself first.
- **Otherwise, the proxy.** Everything else, Pluto included, is sent as a
  link to this server's proxy. The TV must then be able to resolve and reach
  the server's hostname over HTTPS.
- **Segment format.** The page tells the receiver whether segments are MPEG-TS
  or fragmented MP4 (from `#EXT-X-MAP`), since the receiver can't detect it.

## Memory

Idle, the server uses about 25 MiB. The hourly build peaks near 150 MiB for
the US and 180 MiB for `us,ca,uk`, then exits.

Two things keep the build small:

- reading the iptv-org API files one record at a time
- keeping only the feed channels a match could use

Most of a feed's channels are never matched, and holding all of them once
took the build past 350 MiB.
