#!/usr/bin/env bash
# SocraticQ Unified Launcher for Debian Linux (Qualcomm QRB2210 Uno Q ARM64 & Laptops)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

WITH_DASHBOARD="auto"
AGENT_MOCK_FLAG=""
CHAT_SERVER="no"

for arg in "$@"; do
  case "$arg" in
    --with-dashboard)
      WITH_DASHBOARD="yes"
      ;;
    --no-dashboard)
      WITH_DASHBOARD="no"
      ;;
    --mock)
      export SOCRATIC_MOCK=1
      AGENT_MOCK_FLAG="--mock"
      ;;
    --chat)
      CHAT_SERVER="yes"
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./launch.sh [OPTIONS]

Options:
  --with-dashboard   Force starting Streamlit Tukey Cockpit (:8501) regardless of RAM
  --no-dashboard     Skip starting Streamlit Tukey Cockpit
  --mock             Force deterministic heuristic/mock mode for agent & engine
  -h, --help         Show this help message
EOF
      exit 0
      ;;
    *)
      echo "[launch.sh] Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

# 1. Load .env and activate .venv if present
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
  echo "[launch.sh] Loaded environment from .env"
fi

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
  echo "[launch.sh] Activated virtualenv at .venv"
fi

PYTHON_BIN="$(command -v python3 || command -v python)"
ARCH="$(uname -m)"

# 2. Inspect RAM (/proc/meminfo on Linux) & print QRB2210 resource summary
TOTAL_RAM_MB=0
AVAIL_RAM_MB=0
if [[ -r /proc/meminfo ]]; then
  TOTAL_KB="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  AVAIL_KB="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
  TOTAL_RAM_MB=$((TOTAL_KB / 1024))
  AVAIL_RAM_MB=$((AVAIL_KB / 1024))
fi

TOTAL_RAM_GB="$(awk -v mb="$TOTAL_RAM_MB" 'BEGIN {printf "%.2f", mb / 1024.0}')"
AVAIL_RAM_GB="$(awk -v mb="$AVAIL_RAM_MB" 'BEGIN {printf "%.2f", mb / 1024.0}')"

echo "============================================================"
echo " SocraticQ Edge Stack Launcher (arch: ${ARCH})"
echo " RAM Total: ${TOTAL_RAM_MB} MB (${TOTAL_RAM_GB} GB) | Available: ${AVAIL_RAM_MB} MB (${AVAIL_RAM_GB} GB)"
echo "============================================================"

if (( TOTAL_RAM_MB > 0 && TOTAL_RAM_MB < 2560 )); then
  echo "[QRB2210 Resource Guard] Detected <2.5 GB RAM edge envelope."
  echo "  Budget: OS+FastAPI+SQLite (~280 MB) + llama-server -t 3 -c 512 (~650-1050 MB) + STT (~120 MB)."
  if [[ ! -e /dev/zram0 ]]; then
    echo "  [Advice] /dev/zram0 not detected. Enable 512 MB LZ4 zram to prevent OOM under burst load:"
    echo "           sudo modprobe zram && sudo zramctl /dev/zram0 --algorithm lz4 --size 512M && sudo mkswap /dev/zram0 && sudo swapon /dev/zram0"
  else
    echo "  [OK] zram swap device (/dev/zram0) active."
  fi
fi

# 3. Child PID management & clean shutdown
PIDS=()

cleanup() {
  trap - INT TERM EXIT
  echo ""
  echo "[launch.sh] Shutting down SocraticQ services..."
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  sleep 1
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  echo "[launch.sh] All child processes stopped."
}

trap cleanup INT TERM EXIT

# 4. Optionally start llama-server on :8081 (-t 3 -c 512)
LLAMA_BIN="${LLAMA_SERVER_BIN:-$(command -v llama-server || true)}"
MODEL_PATH="${SLM_MODEL_PATH:-}"
if [[ -n "$LLAMA_BIN" && -n "$MODEL_PATH" && -f "$MODEL_PATH" ]]; then
  echo "[launch.sh] Starting local SLM: $LLAMA_BIN ($MODEL_PATH) on :8081 (-t 3 -c 512)..."
  "$LLAMA_BIN" \
    -m "$MODEL_PATH" \
    --host 127.0.0.1 \
    --port 8081 \
    -t 3 \
    -c 512 &
  PIDS+=("$!")
else
  echo "[launch.sh] Skipping llama-server (binary or \$SLM_MODEL_PATH not found); using deterministic offline Socratic fallback."
fi

# 5. Start web backend on :8000 — chat_server.py (offline stdlib, --chat) or FastAPI
export RAWTREE_LOCAL_DB="${RAWTREE_LOCAL_DB:-$ROOT_DIR/agent_rawtree/local_rawtree.db}"
if [[ "$CHAT_SERVER" == "yes" ]]; then
  echo "[launch.sh] Starting chat_server.py (offline stdlib) on http://0.0.0.0:8000 ..."
  "$PYTHON_BIN" chat_server.py --host 0.0.0.0 --port 8000 &
else
  ASGI_APP="agent_rawtree.web_app:app"
  if [[ -f "$ROOT_DIR/server.py" ]]; then
    ASGI_APP="server:app"
  fi
  echo "[launch.sh] Starting FastAPI ($ASGI_APP) on http://0.0.0.0:8000 ..."
  "$PYTHON_BIN" -m uvicorn "$ASGI_APP" --host 0.0.0.0 --port 8000 &
fi
PIDS+=("$!")

# 6. Start Autonomous RawTree Agent (15s cadence)
echo "[launch.sh] Starting Autonomous RawTree Agent (--interval 15 ${AGENT_MOCK_FLAG})..."
"$PYTHON_BIN" -m agent_rawtree.autonomous_agent --interval 15 ${AGENT_MOCK_FLAG} &
PIDS+=("$!")

# 7. Start Streamlit Tukey Cockpit (:8501) when --with-dashboard OR total RAM >= 3.5 GB (3584 MB)
START_STREAMLIT="false"
if [[ "$WITH_DASHBOARD" == "yes" ]]; then
  START_STREAMLIT="true"
elif [[ "$WITH_DASHBOARD" == "auto" && "$TOTAL_RAM_MB" -ge 3584 ]]; then
  START_STREAMLIT="true"
fi

if [[ "$START_STREAMLIT" == "true" ]]; then
  echo "[launch.sh] Starting Comissio Tukey Streamlit Cockpit on http://0.0.0.0:8501 ..."
  "$PYTHON_BIN" -m streamlit run analytics_tukey/app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true &
  PIDS+=("$!")
else
  echo "[launch.sh] Skipping Streamlit dashboard on-board (RAM=${TOTAL_RAM_MB} MB < 3584 MB; pass --with-dashboard to force or run off-board)."
fi

echo "[launch.sh] SocraticQ stack running. Press Ctrl+C to stop."
wait -n "${PIDS[@]}" 2>/dev/null || wait "${PIDS[@]}"
