"""API 与大模型输出的数据契约。大模型的回复一律经 Pydantic 强校验后才进入业务逻辑。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------- LLM 输出

Verdict = Literal["mould", "stain_not_mould", "uncertain"]
Activity = Literal["active", "dormant", "unknown"]
UVResponse = Literal["strong_halo", "weak", "none", "unknown"]


class LLMGrade(BaseModel):
    level: Optional[int] = None
    confidence: float = 0.6
    rationale_zh: str = ""
    rationale_en: str = ""

    @field_validator("level")
    @classmethod
    def _lv(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        return max(1, min(5, int(v)))

    @field_validator("confidence", mode="before")
    @classmethod
    def _cf(cls, v: Any) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.6


class LLMRegion(BaseModel):
    id: int
    verdict: Verdict = "uncertain"
    activity: Activity = "unknown"
    uv_response: UVResponse = "unknown"
    note_zh: str = ""
    note_en: str = ""


class MorphologyHint(BaseModel):
    description_zh: str = ""
    description_en: str = ""
    confidence: Literal["low", "medium"] = "low"


class CauseHypothesis(BaseModel):
    cause_zh: str = ""
    cause_en: str = ""
    likelihood: float = 0.5
    evidence_zh: str = ""
    evidence_en: str = ""


class ActionItem(BaseModel):
    priority: Literal["high", "medium", "low"] = "medium"
    text_zh: str = ""
    text_en: str = ""


class LLMResult(BaseModel):
    """模型输出的强校验容器。多余字段丢弃，缺失字段取默认值。"""
    is_wall_surface: bool = True
    reason_code: Optional[str] = None
    grade: Optional[LLMGrade] = None
    regions: List[LLMRegion] = Field(default_factory=list)
    morphology_hint: Optional[MorphologyHint] = None
    cause_hypotheses: List[CauseHypothesis] = Field(default_factory=list)
    extra_actions: List[ActionItem] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------- API 输出

class RegionOut(BaseModel):
    id: int
    bbox: List[float]
    polygon: List[List[float]]
    area_pct: float
    area_cm2: Optional[float] = None
    verdict: Verdict = "uncertain"
    activity: Activity = "unknown"
    uv_response: UVResponse = "unknown"
    note_zh: str = ""
    note_en: str = ""


class MetricsOut(BaseModel):
    coverage_white_pct: float
    coverage_uv_pct: Optional[float] = None
    hidden_spread_ratio: Optional[float] = None
    hidden_spread_display: Literal["primary", "secondary", "hidden"] = "secondary"
    largest_patch_pct: float
    patch_count: int
    count_is_meaningful: bool
    absolute_area_cm2: Optional[float] = None
    scale_source: str = "none"
    uv_quantification: str = "unavailable"


class GradeOut(BaseModel):
    level: int
    key: str
    label_zh: str
    label_en: str
    confidence: float
    source: str
    cv_level: Optional[int] = None
    llm_level: Optional[int] = None
    rationale_zh: str = ""
    rationale_en: str = ""


class AuditOut(BaseModel):
    algo_version: str
    grading_version: str
    model: Optional[str] = None
    llm_attempts: int = 0
    llm_error: Optional[str] = None
    registered: bool = False
    registration_method: str = "none"
    has_uv: bool = False
    elapsed_ms: int = 0


class AnalysisResult(BaseModel):
    task_id: str
    status: Literal["done", "failed", "rejected"] = "done"
    created_at: str
    grade: Optional[GradeOut] = None
    metrics: Optional[MetricsOut] = None
    regions: List[RegionOut] = Field(default_factory=list)
    morphology_hint: Optional[MorphologyHint] = None
    cause_hypotheses: List[CauseHypothesis] = Field(default_factory=list)
    actions: List[ActionItem] = Field(default_factory=list)
    notes_zh: List[str] = Field(default_factory=list)
    notes_en: List[str] = Field(default_factory=list)
    quality_issues: List[Dict[str, str]] = Field(default_factory=list)
    reason_code: Optional[str] = None
    assets: Dict[str, str] = Field(default_factory=dict)
    audit: Optional[AuditOut] = None
