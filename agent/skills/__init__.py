"""Reusable Skill packages and compatibility helpers."""
from __future__ import annotations

from pathlib import Path

from .executor import (
    SkillActivation,
    SkillDependencyError,
    SkillExecutor,
    render_skill_prompt,
)
from .loader import DEFAULT_BUILTIN_DIR, SkillDiagnostic, SkillLoader
from .models import SkillDef, SkillResource
from .parser import SkillError, parse_skill_file
from .registry import SkillRegistry

# Compatibility alias used by existing callers/tests.
AnalysisSkill = SkillDef


def load_skills(root: Path | None = None) -> dict[str, SkillDef]:
    """Compatibility facade; no root means builtin + user sources."""
    if root is not None:
        loader = SkillLoader(builtin_dir=root, user_dir=Path("__disabled_user_skills__"))
    else:
        loader = SkillLoader()
    return loader.load_all()


def get_skill(name: str, root: Path | None = None) -> SkillDef | None:
    loader = (
        SkillLoader(builtin_dir=root, user_dir=Path("__disabled_user_skills__"))
        if root is not None else SkillLoader()
    )
    loader.load_all()
    return loader.get(name)


__all__ = [
    "AnalysisSkill", "DEFAULT_BUILTIN_DIR", "SkillActivation", "SkillDef",
    "SkillDependencyError", "SkillDiagnostic", "SkillError", "SkillExecutor",
    "SkillLoader", "SkillRegistry", "SkillResource", "get_skill", "load_skills",
    "parse_skill_file", "render_skill_prompt",
]
