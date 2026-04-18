#!/bin/bash
# Start the WhatsApp Go bridge on localhost:8080.
# Once running, call Python functions from whatsapp-client/whatsapp.py.
BRIDGE_DIR="$(dirname "$0")/whatsapp-bridge"

if pgrep -f "whatsapp-bridge" > /dev/null 2>&1; then
  echo "Bridge already running."
  exit 0
fi

cd "$BRIDGE_DIR"
./whatsapp-bridge
