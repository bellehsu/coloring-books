# 以「系統管理員」身分在 Windows PowerShell 執行。
# 把 Windows 的 0.0.0.0:3000 轉給 WSL2 的前端，並只開放私人網路的 TCP 3000。
# WSL IP 每次重開機都會變，開機後需重新執行（可重複執行）。
$ErrorActionPreference = 'Stop'
$Port = 3000
$RuleName = "Coloring Books Frontend $Port"

# 優先取預設路由介面的 IP；失敗時退回 hostname -I 的第一個位址
$wslIp = $null
try {
    $wslIp = ((wsl -e sh -c "ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if(\$i==\"src\") {print \$(i+1); exit}}'") | Select-Object -First 1).Trim()
} catch { }
if ($wslIp -notmatch '^\d+\.\d+\.\d+\.\d+$') {
    $wslIp = ((wsl hostname -I) -split '\s+' | Where-Object { $_ })[0]
}
if (-not $wslIp) { throw '取不到 WSL IP；請確認 WSL 已啟動。' }

# 先刪除舊規則（不存在時 netsh 會報錯，忽略即可）
netsh interface portproxy delete v4tov4 listenport=$Port listenaddress=0.0.0.0 2>$null | Out-Null
netsh interface portproxy add v4tov4 listenport=$Port listenaddress=0.0.0.0 connectport=$Port connectaddress=$wslIp

if (-not (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port -Profile Private | Out-Null
}

Write-Host "WSL IP: $wslIp；已轉發 0.0.0.0:$Port -> ${wslIp}:$Port"
netsh interface portproxy show v4tov4
