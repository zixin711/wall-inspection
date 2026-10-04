"""配置层：.env -> Settings；grading.yaml 热加载。"""
from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # LLM
    llm_base_url: str = "http://127.0.0.1:8100/v1"
    llm_api_key: str = "your-api-key"
    llm_model: str = "qwen3.6-35b-a3b"
    llm_timeout_s: float = 90.0
    llm_max_retries: int = 2
    llm_temperature: float = 0.1
    llm_max_tokens: int = 2000
    llm_json_mode: bool = True

    # 参考图锚定
    reference_mode: str = "anchor"          # none | anchor | full
    reference_dir: str = "./reference"
    reference_max_edge: int = 512

    # 分级
    grading_strategy: str = "llm_led"       # llm_led | cv_led
    grading_config: str = "./config/grading.yaml"

    # 图像与算法
    max_upload_mb: int = 12
    analysis_long_edge: int = 1600
    enable_registration: bool = True
    enable_calibration_card: bool = True
    calib_card_width_mm: float = 85.6
    calib_card_height_mm: float = 54.0

    # 服务
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    storage_dir: str = "./data"
    default_locale: str = "zh"
    retention_days: int = 30
    cors_origins: str = ""

    def path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (BASE_DIR / p).resolve()

    @property
    def storage_path(self) -> Path:
        p = self.path(self.storage_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def reference_path(self) -> Path:
        return self.path(self.reference_dir)

    @property
    def cors_list(self) -> List[str]:
        v = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return v or ["*"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ----------------------------------------------------------------------
# grading.yaml 热加载：按 mtime 判断，改文件即生效，无需重启
# ----------------------------------------------------------------------
class _GradingConfig:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mtime: float = -1.0
        self._data: Dict[str, Any] = {}

    def load(self) -> Dict[str, Any]:
        path = get_settings().path(get_settings().grading_config)
        try:
            mtime = os.path.getmtime(path)
        except OSError as exc:  # 配置缺失是部署错误，早失败好过默默跑错
            raise RuntimeError(f"分级配置不存在: {path}") from exc
        with self._lock:
            if mtime != self._mtime:
                with open(path, "r", encoding="utf-8") as fh:
                    self._data = yaml.safe_load(fh) or {}
                self._mtime = mtime
            return self._data


_grading = _GradingConfig()


def grading_config() -> Dict[str, Any]:
    return _grading.load()


def levels() -> List[Dict[str, Any]]:
    return grading_config()["levels"]


def level_by_number(n: int) -> Dict[str, Any] | None:
    for lv in levels():
        if lv["level"] == n:
            return lv
    return None
