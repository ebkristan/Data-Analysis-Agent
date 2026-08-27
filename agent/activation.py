"""Typed per-turn activation contract for Skills, Commands, and internal actions."""
from __future__ import annotations

from dataclasses import dataclass


INTERNAL_ACTIONS = frozenset({
    "ppt_confirm", "ppt_revise",
    "excel_confirm", "excel_revise",
    "report_confirm", "report_revise",
    "dashboard_confirm", "dashboard_revise",
})


@dataclass(frozen=True)
class ActivationContext:
    """Exactly one explicit activation may own a conversation turn."""

    skill_name: str = ""
    command_name: str = ""
    internal_action: str = ""

    def __post_init__(self) -> None:
        active = sum(bool(value) for value in (
            self.skill_name, self.command_name, self.internal_action,
        ))
        if active > 1:
            raise ValueError("skill, command, and internal_action are mutually exclusive")

    @property
    def kind(self) -> str:
        if self.skill_name:
            return "skill"
        if self.command_name:
            return "command"
        if self.internal_action:
            return "internal_action"
        return "none"

    @property
    def name(self) -> str:
        return self.skill_name or self.command_name or self.internal_action

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "name": self.name}

    def to_record(self) -> dict[str, str]:
        """Stable persisted/audited representation with explicit namespaces."""
        return {
            "kind": self.kind,
            "name": self.name,
            "skill_name": self.skill_name,
            "command_name": self.command_name,
            "internal_action": self.internal_action,
        }
