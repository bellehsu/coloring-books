#!/usr/bin/env bash
# 一起啟動後端（FastAPI）與前端（Next.js dev），選擇性也啟動 ComfyUI。Ctrl+C 會全部關閉。
#
# 用法：
#   scripts/dev.sh                 後端 + 前端
#   scripts/dev.sh --comfy         另外啟動 ComfyUI（需在 dev.env 設 COMFY_START_CMD）
#   scripts/dev.sh --backend-only  只啟動後端
#   scripts/dev.sh --frontend-only 只啟動前端（後端要已在 BACKEND_PORT 執行）
#   scripts/dev.sh --dry-run       只做檢查並列出會啟動什麼，不真的啟動
#
# ComfyUI 位址預設讀 config/settings.yaml 的 comfy.url（8188），可用 APP_COMFY__URL 覆寫；
# --comfy 時若該位址已有 ComfyUI 在執行，就略過啟動。
# 設定：scripts/dev.env（從 dev.env.example 複製）。環境變數優先於 dev.env。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${DEV_ENV_FILE:-$ROOT/scripts/dev.env}"

usage() { sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

RUN_BACKEND=1 RUN_FRONTEND=1 RUN_COMFY=0 DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --comfy) RUN_COMFY=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --backend-only) RUN_FRONTEND=0 ;;
    --frontend-only) RUN_BACKEND=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知參數：$arg" >&2; usage >&2; exit 2 ;;
  esac
done

# 讀 dev.env，但不覆蓋已經設定的環境變數
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    key="${line%%=*}"; val="${line#*=}"
    val="${val%\"}"; val="${val#\"}"
    [[ -z "${!key+x}" ]] && export "$key=$val"
  done < "$ENV_FILE"
fi

PYTHON="${PYTHON:-python}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

# 允許區網其他電腦用本機的主機名稱／IP 開開發伺服器（Next 預設會擋 /_next/* 與 HMR WebSocket）。
# 自動加入：主機名稱、完整網域名稱、區網 IPv4；dev.env 的 DEV_ALLOWED_ORIGINS 會再附加上去。
auto_origins="$(hostname 2>/dev/null || true),$(hostname -f 2>/dev/null || true),$(hostname -I 2>/dev/null | tr ' ' ',' || true)"
DEV_ALLOWED_ORIGINS="$(tr 'A-Z' 'a-z' <<<"$auto_origins,${DEV_ALLOWED_ORIGINS:-}" | tr -s ',' | sed 's/^,//; s/,$//')"
export DEV_ALLOWED_ORIGINS

die() { echo "錯誤：$*" >&2; exit 1; }

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

if (( RUN_BACKEND )); then
  [[ -x "$PYTHON" ]] || command -v "$PYTHON" >/dev/null || die "找不到 python：$PYTHON（請在 scripts/dev.env 設定 PYTHON）"
  env -u PYTHONPATH "$PYTHON" -c "import uvicorn" 2>/dev/null || die "$PYTHON 沒有安裝 uvicorn（PYTHON 要指向專案 venv）"
  port_busy "$BACKEND_PORT" && die "埠 $BACKEND_PORT 已被占用（後端）；改 BACKEND_PORT 或先關掉占用的程式"
fi
if (( RUN_FRONTEND )); then
  command -v npm >/dev/null || die "找不到 npm（Node.js 要在 PATH 裡，例如先載入 nvm）"
  [[ -d "$ROOT/frontend/node_modules" ]] || die "前端還沒安裝依賴：cd frontend && npm ci"
  port_busy "$FRONTEND_PORT" && die "埠 $FRONTEND_PORT 已被占用（前端）；改 FRONTEND_PORT 或先關掉占用的程式"
fi
# 後端實際會用的 ComfyUI 位址：APP_COMFY__URL，否則 settings.yaml 的 comfy.url
comfy_url() {
  if [[ -n "${APP_COMFY__URL:-}" ]]; then echo "${APP_COMFY__URL%/}"; return; fi
  env -u PYTHONPATH "$PYTHON" -c 'import sys; sys.path.insert(0, "backend")
from app.config import load_settings; print(load_settings().comfy.url)' 2>/dev/null \
    || echo "（讀不到 config/settings.yaml 的 comfy.url）"
}
comfy_up() { curl -s -m 3 -o /dev/null -w '%{http_code}' "$1/system_stats" 2>/dev/null | grep -q '^200$'; }

cd "$ROOT"
COMFY_URL="$(comfy_url)"
if (( RUN_COMFY )); then
  if comfy_up "$COMFY_URL"; then
    echo "ComfyUI 已在 $COMFY_URL 執行，略過啟動。"
    RUN_COMFY=0
  else
    [[ -n "${COMFY_START_CMD:-}" ]] || die "--comfy 需要在 scripts/dev.env 設定 COMFY_START_CMD"
    cmd_port="$(grep -oE -- '--port[ =]+[0-9]+' <<<"$COMFY_START_CMD" | grep -oE '[0-9]+$' || true)"
    url_port="$(grep -oE ':[0-9]+$' <<<"$COMFY_URL" | tr -d : || true)"
    if [[ -n "$cmd_port" && -n "$url_port" && "$cmd_port" != "$url_port" ]]; then
      die "COMFY_START_CMD 用埠 $cmd_port，但後端會連 $COMFY_URL；請讓兩者一致（改 COMFY_START_CMD 或設 APP_COMFY__URL）"
    fi
  fi
fi

if (( DRY_RUN )); then
  echo "dry-run：檢查通過，將啟動："
  (( RUN_COMFY )) && echo "  comfy     $COMFY_START_CMD"
  (( RUN_BACKEND )) && echo "  backend   127.0.0.1:$BACKEND_PORT（ComfyUI：$COMFY_URL）"
  (( RUN_FRONTEND )) && echo "  frontend  localhost:$FRONTEND_PORT → 後端 127.0.0.1:$BACKEND_PORT"
  (( RUN_FRONTEND )) && echo "  允許的開發來源：$DEV_ALLOWED_ORIGINS"
  exit 0
fi

PIDS=()
start() {  # start <名稱> <指令>：在獨立 process group 執行，輸出加上前綴
  local name="$1" cmd="$2"
  setsid bash -c "$cmd" > >(sed -u "s/^/[$name] /") 2>&1 &
  PIDS+=("$!")
}

STOPPING=0
cleanup() {
  trap - INT TERM EXIT
  STOPPING=1
  echo; echo "關閉中…"
  for pid in "${PIDS[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  sleep 2
  for pid in "${PIDS[@]}"; do kill -KILL -- "-$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

if (( RUN_COMFY )); then
  start comfy "$COMFY_START_CMD"
fi
if (( RUN_BACKEND )); then
  start backend "exec env -u PYTHONPATH '$PYTHON' -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $BACKEND_PORT"
fi
if (( RUN_FRONTEND )); then
  start frontend "cd frontend && BACKEND_URL=http://127.0.0.1:$BACKEND_PORT exec npm run dev -- -p $FRONTEND_PORT"
fi

echo "已啟動（Ctrl+C 全部關閉）："
(( RUN_BACKEND )) && echo "  後端    http://127.0.0.1:$BACKEND_PORT/api/system/preflight"
(( RUN_FRONTEND )) && echo "  前端    http://localhost:$FRONTEND_PORT（區網：http://$(hostname | tr 'A-Z' 'a-z'):$FRONTEND_PORT）"
echo "  ComfyUI $COMFY_URL$( (( RUN_COMFY )) && echo '（由本腳本啟動）' || true)"
comfy_up "$COMFY_URL" || (( RUN_COMFY )) || echo "  注意：$COMFY_URL 目前連不上 ComfyUI，生成會排隊等待；可加 --comfy 一起啟動。"

# 任何一個服務結束就全部關閉
wait -n "${PIDS[@]}" || true
(( STOPPING )) || echo "有服務已結束，關閉其餘服務。"
