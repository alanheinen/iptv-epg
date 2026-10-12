# Security

## Deploying safely

Signal is meant for your own machine or home network. Never expose it to the
internet.

Its stream proxy fetches whatever URL it's asked to. On a public address,
that makes it an open relay: anyone could send traffic through your
connection, and that traffic would come from your IP address. There's no
authentication, by design, since it's meant for a trusted network.

What's already in place:

- **Local by default.** Every install method listens on the local machine
  only until you change one setting.
- **No LAN targets.** The proxy refuses URLs that resolve to private,
  loopback, link-local, reserved or multicast addresses, including after
  redirects. It can't be pointed at your router, your other devices or the
  host itself.
- **Minimal privileges:**
  - The container image runs as an unprivileged user with a read-only root
    filesystem.
  - The Kubernetes manifests meet Pod Security "restricted".
  - The systemd unit runs as a throwaway user in a tight sandbox.
- **No credentials.** Signal holds none and has no accounts or admin
  interface.

To reach it from outside your home, use a VPN to your network (WireGuard,
Tailscale) rather than opening a port.

## What it connects to

- **The guide build:** GitHub (`api.github.com`, `raw.githubusercontent.com`),
  the iptv-org API (`iptv-org.github.io`) and the listings feeds (`i.mjh.nz`,
  `epgshare01.online`). It identifies itself with a User-Agent naming this
  project.
- **The proxy:** whatever stream hosts you watch, and `boot.pluto.tv` for
  Pluto TV.
- **The page:** loads hls.js from `cdnjs.cloudflare.com`, logos from wherever
  iptv-org points, and Google's Cast library (`gstatic.com`) in Chrome and
  Edge.

Signal sends no analytics or telemetry. What you watch is only in the server's
logs when a stream fails, and in your browser's storage (your last channel).

## Reporting a vulnerability

Please report security problems privately, through
[GitHub's private vulnerability reporting](https://github.com/alanheinen/iptv-epg/security/advisories/new),
not in a public issue. Include what you found and how to reproduce it.
