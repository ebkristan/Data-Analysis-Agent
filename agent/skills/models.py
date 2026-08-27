"""Data model for reusable analysis Skills."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SkillSource = Literal["builtin", "user", "workspace", "workflow"]


@dataclass(frozen=True)
class SkillResource:
    kind: Literal["references", "scripts", "assets"]
    relative_path: str
    path: Path
    size: int


@dataclass(frozen=True)
class SkillDef:
    name: str
    description: str
    prompt: str
    path: Path
    icon: str = "🧩"
    allowed_tools: tuple[str, ...] = ()
    source: SkillSource = "builtin"
    resources: tuple[SkillResource, ...] = ()
    display_name: str = ""

    def __post_init__(self) -> None:
        if self.source not in {"builtin", "user", "workspace", "workflow"}:
            raise ValueError(f"invalid skill source: {self.source!r}")
        if not SKILL_NAME_RE.fullmatch(self.name):
            raise ValueError(f"invalid skill name: {self.name!r}")
        if not self.description.strip():
            raise ValueError("skill description is required")
        if not self.prompt.strip():
            raise ValueError("skill prompt is required")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError(f"duplicate allowed tools for skill {self.name!r}")

    def to_public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "display_name": self.display_name or self.name,
            "description": self.description,
            "icon": self.icon,
            "source": self.source,
        }
