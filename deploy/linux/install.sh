#!/bin/sh
# Install Signal TV Guide as a systemd service, or remove it.
#
#   sudo deploy/linux/install.sh              install or update, then (re)start
#   sudo deploy/linux/install.sh --uninstall  stop and remove everything
#
# Copies the app to $PREFIX (default /opt/signal-tv), installs
# signal-tv.service, and writes /etc/signal-tv.env on first install only, so
# your settings survive updates. Needs Python 3.9+ and systemd; nothing else.
set -eu

PREFIX=${PREFIX:-/opt/signal-tv}
UNIT=/etc/systemd/system/signal-tv.service
ENVFILE=/etc/signal-tv.env
SRC=$(cd "$(dirname "$0")/../.." && pwd)

die() { echo "install.sh: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root (sudo $0)"
command -v systemctl >/dev/null || die "systemd is required"

if [ "${1:-}" = "--uninstall" ]; then
    systemctl disable --now signal-tv.service 2>/dev/null || true
    rm -f "$UNIT"
    systemctl daemon-reload
    rm -rf "$PREFIX" /var/lib/private/signal-tv /var/lib/signal-tv
    echo "Removed signal-tv. $ENVFILE is kept; delete it if you don't need your settings."
    exit 0
fi
[ $# -eq 0 ] || die "unknown option: $1 (use --uninstall or nothing)"

PYTHON=$(command -v python3 || true)
[ -n "$PYTHON" ] || die "python3 not found; install Python 3.9 or newer"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 9))' \
    || die "$PYTHON is $("$PYTHON" -V 2>&1); Python 3.9 or newer is needed"
[ -f "$SRC/scripts/serve.py" ] || die "run this from a copy of the repository ($SRC has no scripts/serve.py)"

# Fresh copy each time, so files removed upstream don't linger.
rm -rf "$PREFIX.new"
mkdir -p "$PREFIX.new/web"
cp -R "$SRC/scripts" "$PREFIX.new/"
cp "$SRC/web/index.html" "$SRC/web/app.css" "$SRC/web/app.js" "$PREFIX.new/web/"
rm -rf "$PREFIX.new/scripts/__pycache__"
rm -rf "$PREFIX"
mv "$PREFIX.new" "$PREFIX"
chmod -R a+rX,go-w "$PREFIX"

sed -e "s|@PYTHON@|$PYTHON|g" -e "s|@PREFIX@|$PREFIX|g" "$SRC/deploy/linux/signal-tv.service" > "$UNIT"

if [ ! -e "$ENVFILE" ]; then
    cat > "$ENVFILE" <<'EOF'
# Signal TV Guide settings. Restart after editing:
#   sudo systemctl restart signal-tv
#
# Countries to build, as iptv-org playlist prefixes (the UK is "uk").
COUNTRIES=us
# 127.0.0.1 serves this machine only. Use 0.0.0.0 to watch from other devices
# on your network, but never expose it to the internet: the stream proxy
# would become an open relay.
HOST=127.0.0.1
PORT=8080
EOF
fi

systemctl daemon-reload
systemctl enable signal-tv.service >/dev/null 2>&1
systemctl restart signal-tv.service

. "$ENVFILE"
case "${HOST:-127.0.0.1}" in 0.0.0.0|::) where=$(hostname) ;; *) where=${HOST:-127.0.0.1} ;; esac
echo "signal-tv is running. The first guide build takes about a minute; then open"
echo "  http://$where:${PORT:-8080}/"
echo "Settings: $ENVFILE   Logs: journalctl -u signal-tv"
