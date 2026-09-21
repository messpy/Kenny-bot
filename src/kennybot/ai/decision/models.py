from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


DECISION_SCHEMA_VERSION = "jev-shadow-v1"


@dataclass(frozen=True)
class DecisionContext:
    """The only message data that may cross the shadow-provider boundary."""

    text: str
    has_reply: bool
    has_mentions: bool
    is_dm: bool
    attachment_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShadowDecision:
    route: str | None = None
    route_probability: float | None = None
    route_probabilities: Mapping[str, float] = field(default_factory=dict)
    external_information_probability: float | None = None
    rag_probability: float | None = None
    server_info_probability: float | None = None
    repair_probability: float | None = None
    latency_ms: float = 0.0
    status: str = "success"
    provider_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "route_probabilities", MappingProxyType(dict(self.route_probabilities)))


@dataclass(frozen=True)
class ShadowEvaluation:
    evaluation_id: str
    decision_schema_version: str = DECISION_SCHEMA_VERSION


@dataclass(frozen=True)
class ShadowMetrics:
    evaluation_id: str
    decision_schema_version: str
    status: str
    latency_ms: float | None = None
    route: str | None = None
    route_probability: float | None = None
    route_probabilities: Mapping[str, float] = field(default_factory=dict)
    external_information_probability: float | None = None
    rag_probability: float | None = None
    server_info_probability: float | None = None
    repair_probability: float | None = None
    existing_raw_plan: object | None = None
    existing_effective_plan: object | None = None
    agreement_raw: bool | None = None
    agreement_effective: bool | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "evaluation_id": self.evaluation_id,
            "decision_schema_version": self.decision_schema_version,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "route": self.route,
            "route_probability": self.route_probability,
            "route_probabilities": dict(self.route_probabilities),
            "external_information_probability": self.external_information_probability,
            "rag_probability": self.rag_probability,
            "server_info_probability": self.server_info_probability,
            "repair_probability": self.repair_probability,
            "existing_raw_plan": self.existing_raw_plan,
            "existing_effective_plan": self.existing_effective_plan,
            "agreement_raw": self.agreement_raw,
            "agreement_effective": self.agreement_effective,
        }
