"""File-based analysis skills exposed as dynamic slash commands.

Skills are intentionally prompt-only: they reuse the agent's audited tool set
instead of importing executable Python from a skill directory.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILLS_DIR = PROJECT_ROOT / "skills"
MAX_SKILL_BYTES = 100_000
MAX_PROMPT_CHARS = 50_000
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)


class SkillError(ValueError):
    """Raised when a SKILL.md file is invalid."""


@dataclass(frozen=True)
class AnalysisSkill:
    name: str
    description: str
    prompt: str
    path: Path
    icon: str = "🧩"

    def to_public_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
        }


def _candidate_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return ()
    direct = sorted(p for p in root.glob("*.md") if p.is_file())
    nested = sorted(p for p in root.glob("*/SKILL.md") if p.is_file())
    return (*direct, *nested)


def parse_skill_file(path: Path) -> AnalysisSkill:
    try:
        if path.stat().st_size > MAX_SKILL_BYTES:
            raise SkillError(f"skill file exceeds {MAX_SKILL_BYTES} bytes")
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"cannot read skill file: {exc}") from exc

    match = _FRONTMATTER_RE.match(raw.lstrip("\ufeff"))
    if not match:
        raise SkillError("missing YAML frontmatter")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillError("frontmatter must be a mapping")

    name = str(meta.get("name", "")).strip()
    description = str(meta.get("description", "")).strip()
    icon = str(meta.get("icon", "🧩")).strip() or "🧩"
    prompt = raw.lstrip("\ufeff")[match.end():].strip()
    if not _NAME_RE.fullmatch(name):
        raise SkillError("name must use lowercase letters, digits, or hyphens")
    if not description:
        raise SkillError("description is required")
    if len(description) > 240:
        raise SkillError("description exceeds 240 characters")
    if not prompt:
        raise SkillError("skill prompt is empty")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise SkillError(f"skill prompt exceeds {MAX_PROMPT_CHARS} characters")
    if len(icon) > 8:
        raise SkillError("icon is too long")

    return AnalysisSkill(name, description, prompt, path, icon)


def load_skills(root: Path | None = None) -> dict[str, AnalysisSkill]:
    """Load valid skills. Duplicate names are resolved deterministically."""
    root = root or DEFAULT_SKILLS_DIR
    skills: dict[str, AnalysisSkill] = {}
    for path in _candidate_files(root):
        try:
            skill = parse_skill_file(path)
        except SkillError as exc:
            log.warning("[skills] skipping %s: %s", path, exc)
            continue
        if skill.name in skills:
            log.warning("[skills] duplicate name %r in %s; keeping %s", skill.name, path, skills[skill.name].path)
            continue
        skills[skill.name] = skill
    return skills


def get_skill(name: str, root: Path | None = None) -> AnalysisSkill | None:
    """Resolve on demand so edited skill files hot-reload between turns."""
    if not _NAME_RE.fullmatch(name or ""):
        return None
    return load_skills(root).get(name)


def render_skill_prompt(skill: AnalysisSkill, user_request: str) -> str:
    """Substitute $ARGUMENTS, or append the request when no placeholder exists."""
    request = (user_request or "").strip()
    if "$ARGUMENTS" in skill.prompt:
        return skill.prompt.replace("$ARGUMENTS", request)
    if request:
        return f"{skill.prompt}\n\n## User request\n\n{request}"
    return skill.prompt
