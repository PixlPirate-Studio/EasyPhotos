#!/bin/sh
# Runs EasyPhotos as the Unraid user (PUID/PGID, default nobody:users = 99:100)
# so files it creates (thumbnails, recycle bin, .xmp) are owned like the rest of your shares.
set -e

umask "${UMASK:-002}"

mkdir -p /config /thumbs 2>/dev/null || true

if [ "$(id -u)" = "0" ] && [ "${PUID:-0}" != "0" ]; then
    chown -R "${PUID}:${PGID}" /config 2>/dev/null || true
    chown "${PUID}:${PGID}" /thumbs 2>/dev/null || true
    export HOME=/config
    exec setpriv --reuid="${PUID}" --regid="${PGID}" --clear-groups "$@"
fi

exec "$@"
