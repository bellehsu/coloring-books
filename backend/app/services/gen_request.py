"""Generation.request_json 的編碼／解碼，以及放在裡面的控制欄位。

request_json 不只是來源紀錄：核心欄位（kind、prompt、seed…）是引擎的輸入；`extra` 還承載
流程與帳務的控制旗標，其他模組會讀、也會改寫它們，所以重建 request_json（例如日後的「重跑」）
必須保留：
- `take`（TAKE）：C 的「換一張／AI 再畫一張」，好了一定換上去（services/pipeline.py 讀）。
- `candidate`（CANDIDATE）：重畫主角一次畫多張時的第 2、3 張：只當候選，自動選圖、頁面流程、
  「還原成自動畫的那張」都不拿它（services/pipeline.py、services/slots.py 讀）。
- `review`（REVIEW）：B 的批次重畫主角的 take：換上去時頁面標成 review_pose（services/pipeline.py 讀）。
- `cleanup`（CLEANUP）：背景清理（「清掉框外雜物」）。來源列被刪掉後 source_generation_id 會變 null，
  靠這個旗標仍認得出是清理：worker 讓它失敗而不是當一般背景畫，失敗也不設 attention、不算「再試一次」
  的失敗（services/worker.py、services/pipeline.py、api/history.py 讀）。
- `cleanup_reserved`（CLEANUP_RESERVED）：`auto_cleanup` 頁的 AI 背景，排隊時已經多預留 1 次給它的自動清理
  （services/jobs.py 寫）。背景成功、排了清理時這 1 次的計數紀錄（usage_day／usage_owner）帶到清理上、旗標改成
  false（services/pipeline.py）；背景失敗／取消時連同清理一起退 2 次（services/usage.py 的 refund_generations）。
- `auto_cleanup`（AUTO_CLEANUP）：自動清理（背景好了自動排的清理，不是按鈕）：好了照新背景的規則選用；
  失敗時改選用來源背景、照常排頁面流程（services/pipeline.py、services/slots.py 讀）。
- `usage_day`／`usage_owner`／`usage_refunded`：當日額度的計數與退回依據（services/usage.py 寫、
  `refund_generations` 改寫）；遺失它們就不會退、或重複退。
- `engine_requeues`／`engine_unavailable_since`：引擎連不上時放回佇列的計數（只有 worker 寫，
  `encode_request` 的 extra 不能帶，在 _CORE_KEYS 裡受保護）。
其餘 extra（pose_mode、background_style、source、uploaded…）才只是來源紀錄，引擎不讀。
"""

from __future__ import annotations

import json

from app.models import Generation, GenKind
from app.services.engine import RETIRED, GenerationRequest
from app.storage import Storage

ENGINE_REQUEUES = "engine_requeues"  # request_json 的計數：因引擎連不上而放回佇列的次數（worker 自己寫）
ENGINE_UNAVAILABLE_SINCE = "engine_unavailable_since"  # request_json：第一次連不上的時間（UTC ISO；worker 自己寫）
TAKE = "take"  # request_json 的旗標：C 的「換一張／AI 再畫一張」，好了一定換上去
CANDIDATE = "candidate"  # request_json 的旗標：重畫主角的第 2、3 張，只當候選、不自動選用
REVIEW = "review"  # request_json 的旗標：批次重畫主角的 take，換上去時頁面標成 review_pose
CLEANUP = "cleanup"  # request_json 的旗標：背景清理（「清掉框外雜物」）
CLEANUP_RESERVED = "cleanup_reserved"  # request_json 的旗標：背景已替它的自動清理多預留 1 次
AUTO_CLEANUP = "auto_cleanup"  # request_json 的旗標：自動清理（不是按鈕）
USAGE_DAY = "usage_day"
USAGE_OWNER = "usage_owner"
USAGE_REFUNDED = "usage_refunded"

# request_json 的核心欄位（extra 不能蓋掉）。TAKE、CANDIDATE、REVIEW、CLEANUP*、AUTO_CLEANUP 與 USAGE_* 是排隊時由 extra 帶入的控制欄位，不在此列
_CORE_KEYS = frozenset({"kind", "prompt", "negative_prompt", "seed", "width", "height", "character_image",
                        "variation", ENGINE_REQUEUES, ENGINE_UNAVAILABLE_SINCE})


def encode_request(
    *,
    kind: GenKind,
    prompt: str,
    negative_prompt: str,
    seed: int,
    width: int,
    height: int,
    character_image: str | None = None,
    variation: str | None = None,
    extra: dict | None = None,
) -> str:
    """產生 Generation.request_json；character_image 為 Storage 的物件 key（來源紀錄，不存 bytes），variation 一律寫入（可為 null）。
    extra 併入 JSON：來源紀錄（pose_mode、background_style…）與控制旗標（TAKE、CANDIDATE、REVIEW、CLEANUP、USAGE_*，見模組說明）；
    引擎只讀核心欄位，extra 不能蓋掉核心欄位。"""
    d = {
        "kind": GenKind(kind).value,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "seed": seed,
        "width": width,
        "height": height,
        "character_image": character_image,
        "variation": variation,
    }
    if extra:
        clash = _CORE_KEYS & set(extra)
        if clash:
            raise ValueError(f"extra 不能覆寫核心欄位：{', '.join(sorted(clash))}")
        d.update(extra)
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def _flag(gen: Generation, key: str) -> bool:
    """request_json 的控制旗標是不是 true（壞掉的 JSON 當成沒有）。"""
    try:
        d = json.loads(gen.request_json or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(d, dict) and d.get(key) is True


def with_flag(request_json: str, key: str, value: bool) -> str:
    """request_json 的控制旗標改成 value（其他欄位不動）。"""
    d = json.loads(request_json)
    d[key] = value
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def wants_take(gen: Generation) -> bool:
    return _flag(gen, TAKE)


def is_candidate(gen: Generation) -> bool:
    return _flag(gen, CANDIDATE)


def wants_review(gen: Generation) -> bool:
    return _flag(gen, REVIEW)


def is_cleanup(gen: Generation) -> bool:
    """背景清理產生的（或要產生的）generation：有來源列，或來源列已刪掉但 request_json 帶 cleanup 旗標。"""
    return gen.source_generation_id is not None or _flag(gen, CLEANUP)


def is_auto_cleanup(gen: Generation) -> bool:
    """自動清理（auto_cleanup 頁的背景好了自動排的），不是按鈕的。"""
    return is_cleanup(gen) and _flag(gen, AUTO_CLEANUP)


def cleanup_reserved(gen: Generation) -> bool:
    return _flag(gen, CLEANUP_RESERVED)


def is_retired(d: dict) -> bool:
    """需要已停用路線的請求：舊整頁流程，或明確要求 SDXL 的快速動作／細緻背景。
    沒有 pose_mode／background_style 的列照 Qwen 執行（新列只把它們當來源紀錄）。"""
    return d.get("kind") == GenKind.PAGE.value or d.get("pose_mode") == "fast" \
        or d.get("background_style") == "detailed"


def decode_request(text: str, storage: Storage, source_key: str | None = None) -> GenerationRequest:
    """還原 GenerationRequest；character_image 的 key 從 Storage 讀成 bytes。
    source_key 是背景清理要編輯的那筆 generation 的圖檔 key（worker 由 Generation.source_generation_id 查出，
    不放在 request_json，避免與資料列重複）；同樣讀成 bytes 放進 source_image。
    key 不合法丟 ValueError；物件不存在丟 StorageNotFound。
    需要已停用路線的請求丟 ValueError(RETIRED)。舊列多出的 structure_* 等欄位忽略。"""
    d = json.loads(text)
    if is_retired(d):
        raise ValueError(RETIRED)
    rel = d.get("character_image")
    return GenerationRequest(
        kind=GenKind(d["kind"]),
        prompt=d["prompt"],
        seed=d["seed"],
        width=d["width"],
        height=d["height"],
        character_image=None if rel is None else storage.get(rel),
        source_image=None if source_key is None else storage.get(source_key),
        variation=d.get("variation"),
    )
