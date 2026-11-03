"""Data structures for VAVE Project Manager specifications and evaluations."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


def _text(value: Any, default: str = "") -> str:
    """Return bounded model text without trusting its input type."""
    if value is None:
        return default
    return str(value).strip()


def _text_list(
    value: Any,
    *,
    limit: int = 50,
    max_chars: int = 2000,
) -> List[str]:
    """Normalize a model-produced string/list into non-empty strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    normalized = []
    for item in value[:limit]:
        text = _text(item)
        if text:
            normalized.append(text[:max_chars])
    return normalized


@dataclass
class Deliverable:
    path: str
    description: str = ""
    status: str = "pending"


@dataclass
class ManagerTask:
    id: str
    engine: str  # "opencode", "vave_swarm", "openclaw", "local_gpu"
    prompt: str
    target_files: List[str] = field(default_factory=list)
    result: str = ""
    status: str = "pending"


@dataclass
class VisionSpec:
    goal: str
    summary: str = ""
    acceptance_criteria: List[str] = field(default_factory=list)
    test_command: str = ""
    tasks: List[ManagerTask] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VisionSpec":
        tasks_data = data.get("tasks", [])
        if not isinstance(tasks_data, list):
            tasks_data = []
        tasks = []
        for t in tasks_data[:20]:
            if isinstance(t, dict):
                tasks.append(
                    ManagerTask(
                        id=_text(t.get("id"), "task")[:80] or "task",
                        engine=_text(t.get("engine"), "vave_swarm")[:40]
                        or "vave_swarm",
                        prompt=_text(t.get("prompt"))[:20000],
                        target_files=_text_list(
                            t.get("target_files"),
                            limit=50,
                            max_chars=1000,
                        ),
                        result=_text(t.get("result"))[:8000],
                        status=_text(t.get("status"), "pending")[:40]
                        or "pending",
                    )
                )
        return cls(
            goal=_text(data.get("goal")),
            summary=_text(data.get("summary"))[:8000],
            acceptance_criteria=_text_list(data.get("acceptance_criteria")),
            test_command=_text(data.get("test_command"))[:2000],
            tasks=tasks,
        )


@dataclass
class EvaluationResult:
    approved: bool
    test_output: str = ""
    test_passed: bool = True
    remarks: str = ""
    missing_criteria: List[str] = field(default_factory=list)
    cycle: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
