"""Inline Skill rendering and audited tool restriction calculation."""
from __future__ import annotations

from dataclasses import dataclass

from agent.tools.registry import BUILTIN_TOOL_REGISTRY

from .models import SkillDef, SkillResource


class SkillDependencyError(ValueError):
    """A Skill requests a tool that is not registered/audited."""


@dataclass(frozen=True)
class SkillActivation:
    name: str
    prompt: str
    requested_tools: frozenset[str]
    resources: tuple[SkillResource, ...]

    def filter_exposed_tools(self, exposed_names: set[str]) -> set[str]:
        """Restrict an already-authorized set; never activate a hidden tool."""
        if not self.requested_tools:
            return set(exposed_names)
        return set(exposed_names) & set(self.requested_tools)


def render_skill_prompt(skill: SkillDef, user_request: str) -> str:
    request = (user_request or "").strip()
    if "$ARGUMENTS" in skill.prompt:
        prompt = skill.prompt.replace("$ARGUMENTS", request)
    elif request:
        prompt = f"{skill.prompt}\n\n## User request\n\n{request}"
    else:
        prompt = skill.prompt
    if skill.resources:
        index = "\n".join(
            f"- {resource.relative_path} ({resource.kind}, {resource.size} bytes)"
            for resource in skill.resources
        )
        prompt += (
            "\n\n## Bundled resources\n"
            "Load only the specific resource needed for the current request. "
            "Scripts are resources and are not automatically executable.\n" + index
        )
    return prompt


class SkillExecutor:
    def activate(self, skill: SkillDef, user_request: str) -> SkillActivation:
        known = BUILTIN_TOOL_REGISTRY.names()
        missing = set(skill.allowed_tools) - known
        if missing:
            raise SkillDependencyError(
                f"Skill {skill.name!r} requires unregistered tools: {', '.join(sorted(missing))}"
            )
        return SkillActivation(
            name=skill.name,
            prompt=render_skill_prompt(skill, user_request),
            requested_tools=frozenset(skill.allowed_tools),
            resources=skill.resources,
        )
