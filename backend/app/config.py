"""設定載入：config/settings.yaml 為唯一來源，環境變數（前綴 APP_）可覆寫。

優先順序（高 → 低）：環境變數 > settings.yaml > 程式預設值。
巢狀欄位用兩個底線：APP_COMFY__URL、APP_PATHS__STORAGE_DIR。
相對路徑一律以專案根目錄為基準解析成絕對路徑。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator
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
    # worker 遇到 ComfyUI 無法連線時，重試前等待的秒數（0 代表立即重試）
    retry_backoff_s: float = Field(10.0, ge=0)


class PathSettings(BaseModel):
    workflows_dir: Path
    storage_dir: Path
    database: Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    comfy: ComfySettings
    paths: PathSettings
    queue: QueueSettings = QueueSettings()

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
