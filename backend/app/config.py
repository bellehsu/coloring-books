"""設定載入：config/settings.yaml 為唯一來源，環境變數（前綴 APP_）可覆寫。

優先順序（高 → 低）：環境變數 > settings.yaml > 程式預設值。
巢狀欄位用兩個底線：APP_COMFY__URL、APP_PATHS__STORAGE_DIR。
相對路徑一律以專案根目錄為基準解析成絕對路徑。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# backend/app/config.py → 專案根目錄
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILE_ENV = "APP_SETTINGS_FILE"
DEFAULT_SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.yaml"


class SettingsError(RuntimeError):
    """設定檔缺漏或路徑錯誤。problems 列出所有問題，方便一次修完。"""

    def __init__(self, problems: list[str], source: Path | None = None):
        self.problems = problems
        self.source = source
        header = f"設定錯誤（{source}）" if source else "設定錯誤"
        super().__init__(header + "：\n" + "\n".join(f"  - {p}" for p in problems))


class ComfySettings(BaseModel):
    url: str = "http://127.0.0.1:8188"
    models_root: Path
    install_dir: Path | None = None
    request_timeout_s: float = Field(30.0, gt=0)
    run_timeout_s: float = Field(900.0, gt=0)

    @field_validator("url")
    @classmethod
    def _strip_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("comfy.url 必須以 http:// 或 https:// 開頭")
        return v

    @property
    def ws_url(self) -> str:
        return "ws" + self.url[len("http"):] + "/ws"


class QueueSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")  # 舊設定檔的 max_wait_s 等欄位直接忽略

    # worker 遇到 ComfyUI 無法連線時，重試前等待的秒數（0 代表立即重試）
    retry_backoff_s: float = Field(10.0, ge=0)


class PathSettings(BaseModel):
    workflows_dir: Path
    storage_dir: Path
    database: Path


class R2Settings(BaseModel):
    endpoint: str | None = None
    bucket: str | None = None
    # 憑證只能來自環境變數（APP_STORAGE__R2__ACCESS_KEY／SECRET_KEY）；SecretStr 讓 repr／log 只顯示 ****
    access_key: SecretStr | None = None
    secret_key: SecretStr | None = None


class StorageSettings(BaseModel):
    backend: Literal["local", "r2"] = "local"
    r2: R2Settings = R2Settings()
    # 簽章網址有效秒數；網址拿到就立刻用（302 轉址），所以很短
    signed_url_ttl_s: int = Field(120, gt=0)
    # R2 物件的本機快取（只是加速，隨時可刪）；None＝<paths.storage_dir>/r2_cache。實際放在 <cache_dir>/<bucket>
    cache_dir: Path | None = None
    cache_max_bytes: int = Field(2 * 1024**3, gt=0)
    # LocalStorage 的 HMAC 金鑰；只能來自環境變數 APP_STORAGE__SIGNING_KEY，
    # 沒設時自動產生並存在 <paths.storage_dir>/.signing_key（0600）
    signing_key: SecretStr | None = None


class AuthSettings(BaseModel):
    # 本機開發：沒有 cookie 的請求視為這個名字的 admin owner（沒有就建立）。雲端一定要留空（None）
    dev_owner: str | None = None
    # cb_session cookie 是否加 Secure（HTTPS 部署時開啟；本機 http 開發要關）
    cookie_secure: bool = False
    # scripts/invites.py 印出的連結前綴（網站對外網址，例如 https://books.example）；None＝http://localhost:3000
    invite_base_url: str | None = None
    # 邀請連結加密存放（owner.token_encrypted）用的金鑰；只能來自環境變數 APP_AUTH__LINK_KEY，
    # 沒設時自動產生並存在 <paths.storage_dir>/.link_key（0600）
    link_key: SecretStr | None = None

    @field_validator("dev_owner")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        v = v.strip() if v is not None else None
        return v or None


# 只能用環境變數設定的欄位（設定檔裡出現就報錯，避免憑證進到檔案或版控）
_ENV_ONLY_FIELDS = {
    ("storage", "r2", "access_key"): "APP_STORAGE__R2__ACCESS_KEY",
    ("storage", "r2", "secret_key"): "APP_STORAGE__R2__SECRET_KEY",
    ("storage", "signing_key"): "APP_STORAGE__SIGNING_KEY",
    ("auth", "link_key"): "APP_AUTH__LINK_KEY",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    comfy: ComfySettings
    paths: PathSettings
    queue: QueueSettings = QueueSettings()
    storage: StorageSettings = StorageSettings()
    auth: AuthSettings = AuthSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # YAML 內容以 init kwargs 傳入；環境變數要能蓋過 YAML，所以 env 排在前面
        return (env_settings, init_settings)

    def resolved(self, root: Path = PROJECT_ROOT) -> "Settings":
        """回傳所有路徑都已轉成絕對路徑的副本。"""

        def abs_(p: Path | None) -> Path | None:
            if p is None:
                return None
            p = Path(os.path.expanduser(str(p)))
            return p if p.is_absolute() else (root / p).resolve()

        return self.model_copy(
            update={
                "comfy": self.comfy.model_copy(
                    update={"models_root": abs_(self.comfy.models_root), "install_dir": abs_(self.comfy.install_dir)}
                ),
                "paths": self.paths.model_copy(
                    update={
                        "workflows_dir": abs_(self.paths.workflows_dir),
                        "storage_dir": abs_(self.paths.storage_dir),
                        "database": abs_(self.paths.database),
                    }
                ),
                "storage": self.storage.model_copy(update={"cache_dir": abs_(self.storage.cache_dir)}),
            }
        )


def settings_file_path() -> Path:
    env = os.environ.get(SETTINGS_FILE_ENV)
    return Path(env).expanduser().resolve() if env else DEFAULT_SETTINGS_FILE


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SettingsError(
            [f"找不到設定檔 {path}。請複製 config/settings.example.yaml 為 config/settings.yaml，"
             f"或以 {SETTINGS_FILE_ENV} 指定其他檔案。"],
            path,
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise SettingsError([f"YAML 解析失敗：{e}"], path) from e
    if not isinstance(data, dict):
        raise SettingsError(["設定檔頂層必須是 mapping"], path)
    return data


def _env_only_problems(data: dict[str, Any]) -> list[str]:
    """設定檔裡不可出現憑證欄位；訊息只列欄位名稱，不含值。"""
    problems: list[str] = []
    for loc, env_name in _ENV_ONLY_FIELDS.items():
        node: Any = data
        for part in loc:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        else:
            problems.append(f"{'.'.join(loc)} 不可寫在設定檔，請改用環境變數 {env_name}")
    return problems


def validate_paths(s: Settings) -> list[str]:
    """檢查路徑是否存在。回傳問題清單（空清單代表通過）。s 必須已 resolved()。"""
    problems: list[str] = []
    if not s.comfy.models_root.is_dir():
        problems.append(f"comfy.models_root 不存在或不是資料夾：{s.comfy.models_root}")
    if s.comfy.install_dir is not None:
        if not s.comfy.install_dir.is_dir():
            problems.append(f"comfy.install_dir 不存在：{s.comfy.install_dir}")
        elif not (s.comfy.install_dir / "main.py").is_file():
            problems.append(f"comfy.install_dir 看起來不是 ComfyUI 安裝目錄（缺 main.py）：{s.comfy.install_dir}")
    if not s.paths.workflows_dir.is_dir():
        problems.append(f"paths.workflows_dir 不存在：{s.paths.workflows_dir}")
    if not s.paths.storage_dir.is_dir():
        problems.append(f"paths.storage_dir 不存在：{s.paths.storage_dir}（請先建立此資料夾）")
    if not s.paths.database.parent.is_dir():
        problems.append(f"paths.database 的父資料夾不存在：{s.paths.database.parent}")
    return problems


def load_settings(path: Path | None = None, *, check_paths: bool = True) -> Settings:
    """讀取設定、套用環境變數、解析成絕對路徑；check_paths=True 時驗證路徑存在。

    任何問題都以 SettingsError 一次列出。
    """
    path = path or settings_file_path()
    data = _read_yaml(path)
    problems = _env_only_problems(data)
    if problems:
        raise SettingsError(problems, path)
    try:
        settings = Settings(**data).resolved()
    except ValidationError as e:
        problems = [
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        ]
        raise SettingsError(problems, path) from e
    if check_paths:
        problems = validate_paths(settings)
        if problems:
            raise SettingsError(problems, path)
    return settings
