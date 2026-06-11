#!/bin/zsh
# ImageGen Studio launcher — double-click to start.
# Starts the local web UI, opens it in your browser, and (if you use Pinokio)
# starts ComfyUI for you. Otherwise just make sure ComfyUI is running yourself.

DIR="$(cd "$(dirname "$0")" && pwd)"
UIPORT="${IMAGEGEN_UI_PORT:-7866}"
COMFY_URL="${COMFY_HOST:-http://localhost:8188}"

echo "Starting ImageGen Studio..."

# 1) Start the web UI if it isn't running yet
if ! curl -s -o /dev/null "http://localhost:$UIPORT/" 2>/dev/null; then
  nohup python3 "$DIR/ui_server.py" >/tmp/imagegen-ui.log 2>&1 &
  sleep 2
fi

# 2) Make sure ComfyUI is up. If Pinokio's pterm is present, start it automatically.
if ! curl -s -o /dev/null "$COMFY_URL/system_stats" 2>/dev/null; then
  PTERM="$HOME/pinokio/bin/npm/bin/pterm"
  if [ -x "$PTERM" ] && [ -d "$HOME/pinokio/api/comfy.git" ]; then
    echo "Starting ComfyUI via Pinokio (this can take ~20-40s)..."
    "$PTERM" run "$HOME/pinokio/api/comfy.git" --default 'run.js?mode=Default' --default run.js \
      >/tmp/imagegen-comfy.log 2>&1 &
    for i in {1..30}; do
      curl -s -o /dev/null "$COMFY_URL/system_stats" 2>/dev/null && break
      sleep 2
    done
  else
    echo "Note: ComfyUI does not seem to be running at $COMFY_URL."
    echo "Start ComfyUI, then reload the page in your browser."
  fi
fi

# 3) Open it
open "http://localhost:$UIPORT/" 2>/dev/null || xdg-open "http://localhost:$UIPORT/" 2>/dev/null

echo ""
echo "ImageGen Studio is open in your browser. You can close this window."
