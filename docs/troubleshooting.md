# Troubleshooting

Start with the logs, which show every guide build and every failed proxy
request with its upstream URL:

| Method | Logs |
| --- | --- |
| Docker | `docker compose logs -f` |
| Kubernetes | `kubectl -n signal-tv logs deploy/signal-tv` |
| Linux | `journalctl -u signal-tv -f` |
| Windows | `%LOCALAPPDATA%\SignalTV\signal-tv.log` |
| From a checkout | the terminal |

A healthy build ends with a line like
`done: 2239 channels, 1049 with guide data -> /data`.

## The page says the data is missing, or shows no channels

The first guide build runs at startup and takes about a minute (`/healthz`
returns 503 until it's done). If it doesn't finish:

- **Look for a failed build** in the logs. A build that fails is retried
  every 10 minutes.
- **Network access.** The build needs outbound HTTPS to
  `raw.githubusercontent.com`, `api.github.com`, `iptv-org.github.io`,
  `i.mjh.nz` and `epgshare01.online`.
- **GitHub API rate limit.** The playlist listing uses the GitHub API, which
  allows 60 unauthenticated requests an hour per IP address. When it's
  refused, the build falls back to a built-in list of the main playlists and
  says so in the log.
- **Memory.** Under 256 MiB, the build can be killed while parsing the
  feeds. The log stops mid-build; Kubernetes shows `OOMKilled`.

## A channel won't play

Most often it's the stream, not Signal. iptv-org's streams are third-party,
and many are offline, geo-blocked or flaky on any given day.

- **Read the error.** The player says why each stream failed, then tries the
  channel's other streams.
- **Geo-blocking.** Streams tagged geo-blocked only play from their own country.
  "Hide geo-blocked" filters out channels whose streams are all tagged.
- **Try VLC or mpv.** The copy button on the row gives a URL for them.
- **Check the logs.** A failed proxy request is logged with the upstream URL
  and status, such as `404` or a timeout.

### Pluto TV channels fail

Pluto channels only play through the server, never directly. If they all
fail:

- **Proxy off.** The header must say "stream proxy on". Without it, the page
  is being served by something other than `serve.py`.
- **Pluto changed something.** The logs show the upstream error for
  `boot.pluto.tv`. Pluto playback copies how Pluto's own site starts a
  session, and can break when Pluto changes it. Please open an issue.

### A channel plays without sound or picture

Some streams use codecs your browser can't decode, such as HEVC in Chrome
without hardware support, or AC-3 audio. Try the channel's other streams, or
VLC.

## Listings are missing or wrong

- **"No listings":** no public feed covers that channel, or its name differs
  too much from the feed's. About half the channels have none.
- **The wrong schedule:** please open an issue with the channel name and its
  `tvg-id` (in `channels.json`). A wrong match is a bug; a missing one is
  usually a gap in the feeds.
- **Listings stop a few hours ahead:** some feeds only run short. Samsung TV
  Plus is often 4 to 10 hours ahead, and the hourly rebuild extends it.
- **A new iptv-org channel has no listings yet:** if it should match an
  epgshare01 feed, it can take until that feed's next daily update (see
  [the feed cache](how-it-works.md#the-feed-cache)).
- **Times look off:** they're shown in your browser's time zone, converted
  from the feeds' UTC times.

## Casting

- **No cast button:** casting needs Chrome or Edge, and the page served by
  `serve.py`.
- **The button is dimmed:** Chrome hasn't found a TV yet. Click it to search
  again. The TV has to be on the same network as your computer, and awake.
- **The TV shows an error, or loads forever:** the TV fetches the stream itself.
  Many streams reach it through this server's proxy, so the TV needs:
  - **HTTPS:** the page must be served over HTTPS with a certificate the TV
    trusts. Google's receiver refuses plain HTTP.
  - **DNS:** the TV must resolve the server's hostname. Some Chromecasts use
    Google's DNS (8.8.8.8) and ignore your router's, so a name that only your
    router knows won't resolve for them. Either give the name a public DNS
    record that points at the private address, or have your router redirect
    port 53 to itself.

## Windows

- **"Python 3.9 or newer wasn't found":** install Python from
  [python.org](https://www.python.org/downloads/) with "Add python.exe to
  PATH" ticked, then run `install.ps1` again. The Microsoft Store's
  `python.exe` placeholder isn't Python.
- **Scripts are blocked:** run it as shown, with
  `powershell -ExecutionPolicy Bypass -File …`.
- **Other devices can't connect:** reinstall with `-Lan` from an administrator
  PowerShell. The firewall rule it adds covers Private networks only, so check
  that Windows treats your network as Private.
- **It doesn't start:** look for "Signal TV Guide" in Task Scheduler, and its
  Last Run Result. The task starts when you log on.

## Linux

- **`systemctl status signal-tv` shows it restarting:** the reason is in
  `journalctl -u signal-tv`. A wrong `HOST` or a port already in use are the
  usual causes.
- **Changes to `/etc/signal-tv.env` don't apply:** restart with
  `sudo systemctl restart signal-tv`.
