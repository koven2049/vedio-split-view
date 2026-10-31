#!/bin/bash
# Process-mode watchdog for the web LaunchAgent (com.koven.vsplit.web).
#
# Why this exists (2026-10-31 S1 outage, ticket T40): launchd KeepAlive only
# revives a *loaded* agent. When the agent gets booted out (restart race,
# manual `launchctl bootout`, stop without a following start), nothing ever
# reloads it — the app log simply ends with no error and the site serves 502
# for hours. The podman→process migration removed the old 30s container
# watchdog; this script restores that guard, launchd-native: every 30s
# (StartInterval, agent com.koven.vsplit.watchdog) re-bootstrap the web agent
# unless `manage.sh stop` flagged a deliberate stop (logs/.service-stopped).
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.koven.vsplit.web"
DOMAIN="gui/$(id -u)/${LABEL}"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
MARKER="${ROOT}/logs/.service-stopped"
LOG="${ROOT}/logs/watchdog.log"

mkdir -p "${ROOT}/logs"

stamp() { date "+%Y-%m-%d %H:%M:%S"; }
note() { echo "$(stamp) $*" >> "${LOG}"; }

# Deliberate stop (`manage.sh stop`) — hands off until the next start clears it.
if [[ -f "${MARKER}" ]]; then
    exit 0
fi

# Agent plist not installed yet (fresh clone before install-launchd) — nothing to guard.
if [[ ! -f "${PLIST}" ]]; then
    exit 0
fi

# Loaded: crash-restart is KeepAlive's job. Stay quiet — no log spam.
if launchctl print "${DOMAIN}" >/dev/null 2>&1; then
    exit 0
fi

if launchctl bootstrap "gui/$(id -u)" "${PLIST}" >>"${LOG}" 2>&1; then
    note "watchdog: ${LABEL} was NOT loaded — bootstrapped"
else
    note "watchdog: bootstrap FAILED for ${LABEL} (launchctl output above)"
fi
exit 0
