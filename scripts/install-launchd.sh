#!/bin/bash
# Install/uninstall the Sentinel always-on supervisor via launchd.
#
# Usage:
#   bash scripts/install-launchd.sh install     # copy plist + load
#   bash scripts/install-launchd.sh uninstall   # unload + remove plist
#   bash scripts/install-launchd.sh status      # show status
#   bash scripts/install-launchd.sh logs        # tail the launchd-stdout log
#
# What it does:
#   * copies scripts/com.sentinel.run.plist to ~/Library/LaunchAgents/
#   * launchctl load -w   (start now + start on every login)
#
# Reversible: uninstall removes both the loaded service and the plist file.
# No system-wide changes; everything lives in your user's LaunchAgents directory.

set -euo pipefail

PLIST_NAME="com.sentinel.run.plist"
PLIST_SRC="$(cd "$(dirname "$0")/.." && pwd)/scripts/${PLIST_NAME}"
PLIST_DST="${HOME}/Library/LaunchAgents/${PLIST_NAME}"
LABEL="com.sentinel.run"
LOG_FILE="$(cd "$(dirname "$0")/.." && pwd)/logs/launchd-stdout.log"

cmd="${1:-status}"

case "$cmd" in
  install)
    mkdir -p "${HOME}/Library/LaunchAgents"
    mkdir -p "$(cd "$(dirname "$0")/.." && pwd)/logs"
    cp "$PLIST_SRC" "$PLIST_DST"
    # Unload first in case an old version is loaded
    launchctl unload -w "$PLIST_DST" 2>/dev/null || true
    launchctl load -w "$PLIST_DST"
    echo "✅ Installed and started: $LABEL"
    echo "   Plist: $PLIST_DST"
    echo "   Logs:  $LOG_FILE"
    echo "   Status check: launchctl list | grep sentinel"
    ;;
  uninstall)
    launchctl unload -w "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "✅ Uninstalled $LABEL (plist removed, service stopped)"
    ;;
  status)
    if launchctl list | grep -q "$LABEL"; then
      echo "✅ $LABEL is loaded:"
      launchctl list | grep "$LABEL"
    else
      echo "❌ $LABEL is NOT loaded. Install with:  bash scripts/install-launchd.sh install"
    fi
    ;;
  logs)
    if [[ -f "$LOG_FILE" ]]; then
      tail -f "$LOG_FILE"
    else
      echo "Log file not yet created: $LOG_FILE"
    fi
    ;;
  *)
    echo "Usage: $0 {install|uninstall|status|logs}"
    exit 1
    ;;
esac
