"""Aggregation and weekly reporting over PII-free entity-preflight quality records.

This module reads only the operational log, computes rates and latency, and
reports which alert conditions hold. It opens no surface and starts no watcher.

Every alert threshold lives in the same tracked immutable seed as the decision
thresholds (``policy.POLICY_SEED_PATH`` = ``configs/entity-preflight.json``);
no other file may carry a competing copy.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .audit import (
    DEFAULT_OPERATIONAL_ROOT,
)
from .contracts import JsonValue, VerificationOutcome
from .gate_quality import GateQualityRecord, QualityDecision, decode_quality_event
from .policy import POLICY_SEED_PATH, PolicyError

_P95: float = 0.95


class QualityLogError(RuntimeError):
    """The PII-free operational quality stream could not be decoded."""


class AlertKind(str, Enum):
    """A quality condition that a human must look at."""

    AUTO_NORMALIZATION_SPIKE = "auto_normalization_spike"
    GUARD_BYPASS = "guard_bypass"
    VERIFICATION_FAILURE = "verification_failure"
    LATENCY_P95 = "latency_p95"


@dataclass(frozen=True, slots=True)
class MetricsThresholds:
    """Alert thresholds read from the one tracked preflight seed."""

    min_sample_size: int
    max_auto_normalization_rate: float
    max_bypass_count: int
    max_verification_failure_rate: float
    max_p95_latency_ms: int

    def __post_init__(self) -> None:
        rates = (self.max_auto_normalization_rate, self.max_verification_failure_rate)
        counts = (self.min_sample_size, self.max_bypass_count, self.max_p95_latency_ms)
        if any(not 0.0 <= value <= 1.0 for value in rates) or any(value < 0 for value in counts):
            raise PolicyError("metric thresholds must be calibrated rates and non-negative counts")


@dataclass(frozen=True, slots=True)
class QualityAlert:
    """One threshold breach, described without any personal value."""

    kind: AlertKind
    observed: float
    threshold: float

    def to_event(self) -> dict[str, JsonValue]:
        return {"alert": self.kind.value, "observed": self.observed, "threshold": self.threshold}


@dataclass(frozen=True, slots=True)
class QualityMetrics:
    """Published quality of the preflight over one window of attempts."""

    total: int
    auto_normalization_rate: float
    confirmation_request_rate: float
    unresolved_rate: float
    verification_failure_rate: float
    p95_latency_ms: int
    bypass_count: int

    def to_event(self) -> dict[str, JsonValue]:
        return {
            "metric": "entity_preflight_quality",
            "total": self.total,
            "auto_normalization_rate": self.auto_normalization_rate,
            "confirmation_request_rate": self.confirmation_request_rate,
            "unresolved_rate": self.unresolved_rate,
            "verification_failure_rate": self.verification_failure_rate,
            "p95_latency_ms": self.p95_latency_ms,
            "bypass_count": self.bypass_count,
        }


def load_metrics_thresholds(path: str | Path = POLICY_SEED_PATH) -> MetricsThresholds:
    """Load the tracked alert thresholds. Missing or invalid settings fail closed."""

    try:
        metrics = json.loads(Path(path).read_text(encoding="utf-8"))["metrics"]
        return MetricsThresholds(
            min_sample_size=int(metrics["min_sample_size"]),
            max_auto_normalization_rate=float(metrics["max_auto_normalization_rate"]),
            max_bypass_count=int(metrics["max_bypass_count"]),
            max_verification_failure_rate=float(metrics["max_verification_failure_rate"]),
            max_p95_latency_ms=int(metrics["max_p95_latency_ms"]),
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
        if isinstance(error, PolicyError):
            raise
        raise PolicyError("entity-preflight metric thresholds are unreadable or invalid") from error


def load_quality_records(
    root: str | Path = DEFAULT_OPERATIONAL_ROOT,
) -> tuple[GateQualityRecord, ...]:
    """Decode quality records from the PII-free operational JSONL only."""

    paths = sorted(Path(root).expanduser().glob("entity-preflight*.jsonl"))
    if not paths:
        return ()
    records: list[GateQualityRecord] = []
    try:
        for path in paths:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise QualityLogError("entity-preflight operational event is not an object")
                    record = decode_quality_event(event)
                    if record is not None:
                        records.append(record)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise QualityLogError("entity-preflight operational quality log is unreadable") from error
    return tuple(records)


def aggregate_quality(records: Iterable[GateQualityRecord]) -> QualityMetrics:
    """Summarize one window of guarded attempts.

    Rates are shares of the whole window. The verification-failure rate is the
    share of the attempts that actually reached the connector, because an
    attempt the guard blocked has no readback to fail.
    """

    window = tuple(records)
    verified = [record for record in window if record.verification is not None]
    failed = [record for record in verified if record.verification != VerificationOutcome.MATCH]
    return QualityMetrics(
        total=len(window),
        auto_normalization_rate=_share(window, QualityDecision.AUTO_NORMALIZED),
        confirmation_request_rate=_share(window, QualityDecision.CONFIRMATION_REQUESTED),
        unresolved_rate=_share(window, QualityDecision.UNRESOLVED),
        verification_failure_rate=_rate(len(failed), len(verified)),
        p95_latency_ms=_p95_latency(window),
        bypass_count=sum(1 for record in window if _bypassed(record)),
    )


def evaluate_alerts(
    metrics: QualityMetrics,
    thresholds: MetricsThresholds,
) -> tuple[QualityAlert, ...]:
    """Return every alert condition that currently holds.

    ``GUARD_BYPASS`` ignores the sample-size floor: a single non-automatic
    decision whose external write was not blocked is already a defect, not a
    statistical fluctuation.
    """

    alerts: list[QualityAlert] = []
    if metrics.bypass_count > thresholds.max_bypass_count:
        alerts.append(
            QualityAlert(AlertKind.GUARD_BYPASS, metrics.bypass_count, thresholds.max_bypass_count)
        )
    if metrics.total < thresholds.min_sample_size:
        return tuple(alerts)
    if metrics.auto_normalization_rate > thresholds.max_auto_normalization_rate:
        alerts.append(
            QualityAlert(
                AlertKind.AUTO_NORMALIZATION_SPIKE,
                metrics.auto_normalization_rate,
                thresholds.max_auto_normalization_rate,
            )
        )
    if metrics.verification_failure_rate > thresholds.max_verification_failure_rate:
        alerts.append(
            QualityAlert(
                AlertKind.VERIFICATION_FAILURE,
                metrics.verification_failure_rate,
                thresholds.max_verification_failure_rate,
            )
        )
    if metrics.p95_latency_ms > thresholds.max_p95_latency_ms:
        alerts.append(
            QualityAlert(
                AlertKind.LATENCY_P95, metrics.p95_latency_ms, thresholds.max_p95_latency_ms
            )
        )
    return tuple(alerts)


def _bypassed(record: GateQualityRecord) -> bool:
    """A decision other than automatic normalization must block the external write."""

    return record.decision != QualityDecision.AUTO_NORMALIZED and not record.external_write_blocked


def _share(window: tuple[GateQualityRecord, ...], decision: QualityDecision) -> float:
    return _rate(sum(1 for record in window if record.decision == decision), len(window))


def _rate(part: int, whole: int) -> float:
    return 0.0 if whole == 0 else round(part / whole, 6)


def _p95_latency(window: tuple[GateQualityRecord, ...]) -> int:
    """Nearest-rank p95, so the value is an observed latency and never interpolated."""

    latencies = sorted(record.latency_ms for record in window)
    if not latencies:
        return 0
    rank = max(1, math.ceil(_P95 * len(latencies)))
    return latencies[rank - 1]
