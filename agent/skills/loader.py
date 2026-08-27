"""Multi-source Skill discovery with deterministic precedence and hot reload."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .models import SkillDef, SkillSource
from .parser import SkillError, parse_skill_file
from infrastructure.paths import resource_path

log = logging.getLogger(__name__)

PROJECT_ROOT = resource_path()
DEFAULT_BUILTIN_DIR = resource_path("skills")
DEFAULT_USER_DIR = Path(os.getenv("BAA_SKILLS_DIR", "~/.baa/skills")).expanduser()


@dataclass(frozen=True)
class SkillDiagnostic:
    path: str
    source: str
    error: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "source": self.source, "error": self.error}


class SkillLoader:
    """Load Workspace > user > builtin Skills; higher layers override by name."""

    def __init__(
        self,
        *,
        builtin_dir: Path | None = None,
        user_dir: Path | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self.builtin_dir = builtin_dir if builtin_dir is not None else DEFAULT_BUILTIN_DIR
        self.user_dir = user_dir if user_dir is not None else DEFAULT_USER_DIR
        self.workspace_dir = workspace_dir
        self._skills: dict[str, SkillDef] = {}
        self._cache: dict[str, SkillDef] = {}
        self._diagnostics: list[SkillDiagnostic] = []

    @staticmethod
    def _candidate_files(root: Path | None) -> tuple[Path, ...]:
        if root is None or not root.is_dir():
            return ()
        direct = sorted(path for path in root.glob("*.md") if path.is_file())
        nested = sorted(path for path in root.glob("*/SKILL.md") if path.is_file())
        return (*direct, *nested)

    def _scan(self, root: Path | None, source: SkillSource) -> list[SkillDef]:
        found: list[SkillDef] = []
        seen_in_source: set[str] = set()
        for path in self._candidate_files(root):
            try:
                skill = parse_skill_file(path, source=source)
            except SkillError as exc:
                self._diagnostics.append(SkillDiagnostic(str(path), source, str(exc)))
                log.warning("[skills] skipping %s skill %s: %s", source, path, exc)
                continue
            if skill.name in seen_in_source:
                message = f"duplicate skill name in {source} source: {skill.name}"
                self._diagnostics.append(SkillDiagnostic(str(path), source, message))
                log.warning("[skills] %s", message)
                continue
            seen_in_source.add(skill.name)
            found.append(skill)
        return found

    def load_all(self) -> dict[str, SkillDef]:
        self._diagnostics = []
        merged: dict[str, SkillDef] = {}
        # Low-to-high precedence; assignment by a later layer is intentional.
        for root, source in (
            (self.builtin_dir, "builtin"),
            (self.user_dir, "user"),
            (self.workspace_dir, "workspace"),
        ):
            for skill in self._scan(root, source):
                previous = merged.get(skill.name)
                if previous:
                    log.info(
                        "[skills] %s skill %r overrides %s skill at %s",
                        source, skill.name, previous.source, previous.path,
                    )
                merged[skill.name] = skill
        self._skills = merged
        self._cache.update(merged)
        return dict(merged)

    def get(self, name: str) -> SkillDef | None:
        skill = self._skills.get(name)
        if skill is None:
            return None
        try:
            fresh = parse_skill_file(skill.path, source=skill.source)
        except SkillError as exc:
            log.warning("[skills] hot reload failed for %r, using cache: %s", name, exc)
            return self._cache.get(name, skill)
        if fresh.name != name:
            log.warning("[skills] hot reload changed name %r -> %r; using cache", name, fresh.name)
            return self._cache.get(name, skill)
        self._skills[name] = fresh
        self._cache[name] = fresh
        return fresh

    def reload(self) -> dict[str, SkillDef]:
        return self.load_all()

    def diagnostics(self) -> tuple[SkillDiagnostic, ...]:
        return tuple(self._diagnostics)

    def catalog(self) -> tuple[SkillDef, ...]:
        return tuple(self._skills.values())
