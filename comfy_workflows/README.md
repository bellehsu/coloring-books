# Workflow 資料夾與 manifest 格式

每個 workflow 是一個資料夾：

```
comfy_workflows/
  <name>/
    workflow_api.json   # ComfyUI「Export (API)」匯出的 API 格式（不是 UI 格式）
    manifest.yaml       # 本文件描述的格式
```

後端只接受 manifest `parameters` 白名單內的參數；其他節點輸入一律維持 `workflow_api.json` 的原值。
節點一律以 `_meta.title` 定位（在 ComfyUI 右鍵節點 → Title 設定），不寫死 node id。
JSON schema：`backend/app/schemas/manifest.schema.json`（載入時自動驗證）。

## 版本

- `workflow_hash`：`workflow_api.json` 的 SHA-256
- `manifest_hash`：`manifest.yaml` 的 SHA-256
- `version`：上述兩者合併後 SHA-256 的前 12 碼。任一檔案改動，version 就會改變，生成紀錄可以追溯。

## 欄位

```yaml
schema_version: 1                 # 固定為 1
name: qwen_character              # 必須與資料夾名稱相同；小寫英數與底線
display_name: "..."               # 選填
route: C                          # 所屬路線（目前只有 C）
status: draft                     # draft | ready（資訊用途）
description: "..."
workflow_file: workflow_api.json  # 選填，預設 workflow_api.json
notes: "..."                      # 選填

parameters:                       # 邏輯參數 → 節點輸入
  <參數名>:                       # 小寫英數與底線
    type: int | float | string | bool | enum | image
    targets:                      # 一個參數可以寫入多個節點輸入
      - {title: SAMPLER, input: seed}
    default: ...                  # 選填；沒給值時使用
    required: false               # 選填；true 且沒有 default 時必須由呼叫端提供
    min: 0                        # int / float 必填
    max: 100                      # int / float 必填
    max_length: 2000              # string 選填
    choices: [a, b]               # enum 必填；preflight 會確認每個 choice 都被節點接受
    description: "..."

outputs:                          # 要收集輸出的節點（通常是 SaveImage）
  - {title: OUTPUT, kind: image}

models:                           # 需要的模型檔（preflight 檢查、生成紀錄保存）
  - id: qwen_edit_2511_q4km
    filename: qwen-image-edit-2511-Q4_K_M.gguf  # ComfyUI 下拉選單中看到的名稱
    folder: unet                           # 應放在 models_root 底下哪個子資料夾
    comfy_folder: unet_gguf                # 選填：ComfyUI 的資料夾類型與 folder 不同時填寫
    source: https://huggingface.co/...     # 下載頁面
    source_file: ...                       # 選填：來源檔名（需改名時）
    size_gb: 13.24
    loader: {title: UNET, input: unet_name}  # 選填：載入此模型的節點
    note: "..."

custom_nodes:                     # 選填：preflight 缺節點時提示安裝來源
  - name: ComfyUI-GGUF
    repo: https://github.com/city96/ComfyUI-GGUF
    provides: [UnetLoaderGGUF]
```

## 參數型別

| type | 接受的值 | 檢查 |
|---|---|---|
| `int` | 整數（`3.0` 會轉成 `3`；布林值拒絕） | `min` ≤ 值 ≤ `max` |
| `float` | 數字 | 有限值、`min` ≤ 值 ≤ `max` |
| `string` | 字串 | `max_length` |
| `bool` | `true` / `false` | — |
| `enum` | `choices` 其中之一 | — |
| `image` | 已上傳到 ComfyUI input 的名稱（例如 `coloring_books/abc_ref.png`） | 不可為絕對路徑或含 `..` |

不在 `parameters` 的鍵一律拒絕（`ParameterError`），錯誤會一次列出所有問題。
沒有提供、也沒有 `default` 的選填參數，會沿用 workflow 原值，並一樣記錄到生成紀錄中。

## 載入時的語意檢查

- 每個 `targets[].title`、`outputs[].title`、`models[].loader.title` 都必須在 workflow 中**恰好出現一次**
- `targets[].input` 必須存在於該節點的 inputs，且不能是連線（`["node_id", 0]`）
- 兩個參數不可指向同一個節點輸入
- `default` 必須通過該參數自己的型別與範圍檢查
- `models[].loader` 指向的節點，其輸入值必須等於 `filename`

任何一項失敗，該 workflow 標記為不可用（其他 workflow 不受影響），錯誤會出現在 preflight 報告中。

## Preflight（需要 ComfyUI 執行中）

`python scripts/preflight.py` 或 `GET /api/system/preflight`，對每個 workflow 檢查：

- **缺節點**：workflow 的 `class_type` 不在 `/object_info` 中（附上 `custom_nodes` 的 repo）
- **缺模型**：有 `loader` 時，看該節點在 `/object_info` 的下拉選項；否則看 `GET /models/<comfy_folder 或 folder>`
- **輸入錯誤**：必填輸入缺漏、下拉選單的值不在選項內、enum 參數的 `choices` 不被節點接受
- **輸入警告**：workflow 中有、但節點定義沒有的輸入（ComfyUI 會忽略）

## 新增一個 workflow

1. 在 ComfyUI 做好 workflow，把要開放的節點 title 改成唯一、好懂的名稱（建議全大寫，例如 `SAMPLER`）
2. 「Export (API)」存成 `comfy_workflows/<name>/workflow_api.json`
3. 寫 `manifest.yaml`
4. `python scripts/preflight.py --workflow <name>` 確認全部通過
