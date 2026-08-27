"""Registry for Skill identity; intentionally independent from Commands."""
from __future__ import annotations

from collections.abc import Iterable

from .models import SkillDef


class SkillRegistry:
    def __init__(self, skills: Iterable[SkillDef] = ()) -> None:
        self._skills: dict[str, SkillDef] = {}
        for skill in skills:
            self.register(skill)

    def register(self, skill: SkillDef) -> None:
        if skill.name in self._skills:
            raise ValueError(f"duplicate skill name: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillDef | None:
        return self._skills.get(name)

    def all(self) -> tuple[SkillDef, ...]:
        return tuple(self._skills.values())

