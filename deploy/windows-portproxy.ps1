# 以「系統管理員」身分在 Windows PowerShell 執行。
# 把 Windows 的 0.0.0.0:3000 轉給 WSL2 的前端，並只開放私人網路的 TCP 3000。
# WSL IP 每次重開機都會變，開機後需重新執行（可重複執行）。
# 本檔必須以 UTF-8（含 BOM）儲存：Windows PowerShell 5.1 讀沒有 BOM 的檔案時用 ANSI 字碼頁，中文會變亂碼。
$ErrorActionPreference = 'Stop'
$Port = 3000
$RuleName = "Coloring Books Frontend $Port"

function Test-IPv4([string]$Text) {
    $addr = $null
    return [System.Net.IPAddress]::TryParse($Text, [ref]$addr) -and
        $addr.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
        $Text -match '^\d+\.\d+\.\d+\.\d+$'
}

# 優先取預設路由介面的 IP；失敗時退回 wsl hostname -I 中第一個 IPv4 位址
$wslIp = $null
try {
    $out = (wsl -e ip -4 route get 1.1.1.1) -join ' '
    if ($out -match 'src\s+(\d+\.\d+\.\d+\.\d+)') { $wslIp = $Matches[1] }
} catch { }
if (-not $wslIp) {
    Write-Warning '無法由預設路由取得 WSL IP，改用 wsl hostname -I 的第一個 IPv4 位址（多網卡時可能不正確）。'
    try {
        $wslIp = (wsl hostname -I) -split '\s+' |
            Where-Object { $_ -and (Test-IPv4 $_) } |
            Select-Object -First 1
    } catch { }
}
if (-not $wslIp -or -not (Test-IPv4 $wslIp)) { throw '取不到 WSL 的 IPv4 位址；請確認 WSL 已啟動。' }
Write-Host "WSL IP: ${wslIp}"

# 先刪除舊規則：第一次執行沒有舊規則時 netsh 會報錯，在 Stop 模式下會中止腳本，所以包在 try/catch 裡忽略
try {
    netsh interface portproxy delete v4tov4 listenport=$Port listenaddress=0.0.0.0 2>$null | Out-Null
} catch { }
netsh interface portproxy add v4tov4 listenport=$Port listenaddress=0.0.0.0 connectport=$Port connectaddress=$wslIp
if ($LASTEXITCODE -ne 0) { throw "netsh portproxy add 失敗（exit ${LASTEXITCODE}）" }

if (-not (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port -Profile Private | Out-Null
}

Write-Host "已轉發 0.0.0.0:${Port} -> ${wslIp}:${Port}"
netsh interface portproxy show v4tov4
