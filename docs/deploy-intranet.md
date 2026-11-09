# 內網部署

一台 Windows 工作站（WSL2 Ubuntu 24.04）同時跑後端、前端與 ComfyUI，供辦公室同網段的同事使用。
只有前端 3000 埠對區網開放；後端（8000）與 ComfyUI 只綁 127.0.0.1。瀏覽器只呼叫同源 `/api/*`，由前端轉給後端。

## 1. ComfyUI

照 README 啟動，但要綁在本機：加上 `--listen 127.0.0.1`（埠依 `config/settings.yaml` 的 `comfy.url`，必要時用 `APP_COMFY__URL` 覆寫）。

## 2. 後端（單一 process）

```bash
cd <專案根目錄>
env -u PYTHONPATH <VENV>/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
# 例：<VENV> = /ai-data/venvs/ubuntu24.04/coloring-books
```

- **不可加 `--workers`**：單一 GPU 的生成 worker 在 process 內，啟動時會把 `running` 的任務重新排隊。
- 設定來自 `config/settings.yaml`（由 `config/settings.example.yaml` 複製），可用 `APP_` 環境變數覆寫，例如 `APP_COMFY__URL=http://127.0.0.1:8188`。
- `env -u PYTHONPATH` 只在 shell 有 source ROS 時需要。

## 3. 前端正式建置

```bash
cd frontend
npm ci
BACKEND_URL=http://127.0.0.1:8000 npm run build
cp -r .next/static .next/standalone/.next/static
[ -d public ] && cp -r public .next/standalone/public    # 目前沒有 public 資料夾，有的話才需要
PORT=3000 HOSTNAME=0.0.0.0 node .next/standalone/server.js
```

- **`BACKEND_URL` 在 build 時讀取**（`next.config.ts` 的 rewrites），會被寫死在 standalone 產物裡；啟動時再設定無效。改後端位址要重新 build。
- standalone 不含 `.next/static` 與 `public`，必須如上複製，否則 JS/CSS 會 404。每次重新 build 後都要再複製一次。
- 本專案的 `server.js` 就在 `.next/standalone/server.js`（沒有多一層專案資料夾）；請在 `frontend/` 下執行，或把 `WorkingDirectory` 設為 `frontend/`。若日後 build 輸出變成巢狀路徑，以 `find .next/standalone -name server.js -not -path '*/node_modules/*'` 確認。
- build 會透過 `next/font` 下載 Google Fonts，建置的機器需要連網；執行時不需要。
- `HOSTNAME=0.0.0.0` 是必要的，否則只聽本機。

## 4. 常駐：systemd user service（WSL2）

WSL2 預設沒有 systemd。在 `/etc/wsl.conf` 加入後，於 Windows 執行 `wsl --shutdown` 再重開：

```ini
[boot]
systemd=true
```

```bash
mkdir -p ~/.config/systemd/user
cp deploy/coloring-books-backend.service deploy/coloring-books-frontend.service ~/.config/systemd/user/
# 先編輯兩個檔案：專案路徑、venv 路徑、node 路徑（見檔內註解）
systemctl --user daemon-reload
systemctl --user enable --now coloring-books-backend coloring-books-frontend
loginctl enable-linger $USER      # 沒登入也持續執行
journalctl --user -u coloring-books-backend -f
```

重新 build 前端後：`systemctl --user restart coloring-books-frontend`。
ComfyUI 可另外照同樣方式做一個 unit，或手動啟動。

**沒有 systemd 的替代方案**（`nohup`，或改用 tmux 開三個視窗）：

```bash
cd <專案根目錄>
nohup env -u PYTHONPATH <VENV>/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 > /tmp/cb-backend.log 2>&1 &
cd frontend && PORT=3000 HOSTNAME=0.0.0.0 nohup node .next/standalone/server.js > /tmp/cb-frontend.log 2>&1 &
```

WSL 重啟後不會自動啟動，需手動再執行。

## 5. Windows 埠轉發與防火牆

WSL2 預設是 NAT 網路，區網進不來。以**系統管理員** PowerShell 執行：

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl$\Ubuntu-24.04\<專案根目錄>\deploy\windows-portproxy.ps1
# 或把 deploy\windows-portproxy.ps1 複製到 Windows 資料夾後執行
```

腳本會取得 WSL IP（優先取預設路由介面的位址，失敗才退回 `wsl hostname -I` 的第一個位址，並印出所選 IP）、重設 3000 的 portproxy 規則，並新增只允許「私人」網路設定檔、TCP 3000 的防火牆規則。可重複執行。

- **WSL 重開機後 IP 會變，必須重新執行腳本。**
- 只開 3000。不要轉發 8000 或 ComfyUI。
- 替代方案：在 `%UserProfile%\.wslconfig` 設 `[wsl2]` `networkingMode=mirrored`，WSL 與 Windows 共用 IP，就不需要 portproxy（仍需防火牆規則允許 3000，並可能需要設定 Hyper-V 防火牆）。

## 6. 驗證

1. WSL 內：`curl -s localhost:3000/api/system/queue` 應回 `{"state":"idle",...}`。
2. Windows 本機：瀏覽器開 `http://localhost:3000`。
3. 同網段另一台電腦開 `http://<Windows IP>:3000`（Windows IP 用 `ipconfig` 查），應看到「我的繪本」。
4. 從另一台電腦確認 8000 連不上：`http://<Windows IP>:8000` 應逾時。

## 7. 疑難排解

- **Windows 本機能連、其他電腦連不上**：防火牆規則只開「私人」網路設定檔。若 Windows 把這個區網歸類為「公用」，規則不會生效。檢查：`Get-NetConnectionProfile`（看 `NetworkCategory`）。改成私人（系統管理員 PowerShell）：`Set-NetConnectionProfile -InterfaceAlias "<介面名稱>" -NetworkCategory Private`，或在「設定 > 網路和網際網路 > 內容」選「私人網路」。
- **重開機後連不上**：WSL IP 變了，重新執行 `windows-portproxy.ps1`。
- **前端回 502／API 失敗**：確認後端在 127.0.0.1:8000 運作，且 build 時的 `BACKEND_URL` 正確。
