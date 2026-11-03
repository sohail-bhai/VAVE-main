"""Shared, serializable reliability models."""

from __future__ import annotations

import datetime as _datetime
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List


class CheckStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


class HealthLevel(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass
class HealthCheck:
    name: str
    status: CheckStatus
    summary: str
    critical: bool = False
    repairable: bool = False
    latency_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class HealthReport:
    checks: List[HealthCheck]
    generated_at: str = field(
        default_factory=lambda: _datetime.datetime.now(_datetime.timezone.utc).isoformat()
    )
    repairs: List[Dict[str, Any]] = field(default_factory=list)
    version: int = 1

    @property
    def level(self) -> HealthLevel:
        if any(
            check.status == CheckStatus.FAILED and check.critical
            for check in self.checks
        ):
            return HealthLevel.FAILED
        if any(
            check.status in (CheckStatus.WARNING, CheckStatus.FAILED)
            for check in self.checks
        ):
            return HealthLevel.DEGRADED
        return HealthLevel.HEALTHY

    @property
    def exit_code(self) -> int:
        return 1 if self.level == HealthLevel.FAILED else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "level": self.level.value,
            "checks": [check.to_dict() for check in self.checks],
            "repairs": list(self.repairs),
            "summary": {
                "ok": sum(c.status == CheckStatus.OK for c in self.checks),
                "warning": sum(c.status == CheckStatus.WARNING for c in self.checks),
                "failed": sum(c.status == CheckStatus.FAILED for c in self.checks),
                "skipped": sum(c.status == CheckStatus.SKIPPED for c in self.checks),
            },
        }

    def render_text(self) -> str:
        symbols = {
            CheckStatus.OK: "OK",
            CheckStatus.WARNING: "WARN",
            CheckStatus.FAILED: "FAIL",
            CheckStatus.SKIPPED: "SKIP",
        }
        lines = [f"VAVE Doctor: {self.level.value.upper()}"]
        for check in self.checks:
            latency = f" ({check.latency_ms:.0f} ms)" if check.latency_ms else ""
            lines.append(
                f"[{symbols[check.status]}] {check.name}: {check.summary}{latency}"
            )
        if self.repairs:
            lines.append("")
            lines.append("Safe repairs:")
            for repair in self.repairs:
                state = "OK" if repair.get("ok") else "FAIL"
                lines.append(f"[{state}] {repair.get('action', 'repair')}: {repair.get('message', '')}")
        summary = self.to_dict()["summary"]
        lines.append("")
        lines.append(
            "Summary: "
            f"{summary['ok']} ok, {summary['warning']} warning, "
            f"{summary['failed']} failed, {summary['skipped']} skipped."
        )
        return "\n".join(lines)
