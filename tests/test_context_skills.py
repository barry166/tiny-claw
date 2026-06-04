from __future__ import annotations

from tiny_claw._internal.context.builder import ContextBuilder, PromptComposer
from tiny_claw._internal.context.skills import SkillRegistry, SkillSelector


def test_skill_registry_returns_empty_when_directory_is_missing(tmp_path) -> None:
    registry = SkillRegistry(workdir=tmp_path)

    assert registry.discover() == ()


def test_skill_registry_parses_skill_frontmatter_and_body(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "debug-helper",
        """---
name: debug-helper
description: Diagnose tricky failures
triggers:
  - stack trace
  - traceback
allowed-tools: read,bash
argument-hint: "<error>"
disable-model-invocation: false
user-invocable: true
paths: ["scripts/check.sh", "notes.md"]
---

# Debug Helper

Use $ARGUMENTS from ${TINY_CLAW_SKILL_DIR}.
""",
    )

    skills = SkillRegistry(workdir=tmp_path).discover()

    assert len(skills) == 1
    skill = skills[0]
    assert skill.name == "debug-helper"
    assert skill.description == "Diagnose tricky failures"
    assert skill.triggers == ("stack trace", "traceback")
    assert skill.allowed_tools == ("read", "bash")
    assert skill.argument_hint == "<error>"
    assert skill.paths == ("scripts/check.sh", "notes.md")
    assert "Debug Helper" in skill.body


def test_skill_registry_skips_invalid_skill_directory_name(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "Bad Name",
        """# Missing frontmatter falls back to invalid directory name.""",
    )

    assert SkillRegistry(workdir=tmp_path).discover() == ()


def test_skill_registry_truncates_large_skill_file(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "large-skill",
        """---
name: large-skill
description: Large skill
---

"""
        + ("x" * 100),
    )

    skills = SkillRegistry(workdir=tmp_path, max_skill_file_chars=50).discover()

    assert skills[0].truncated is True
    assert len(skills[0].body) <= 50


def test_skill_selector_supports_explicit_dollar_invocation(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "debug-helper",
        """---
name: debug-helper
description: Diagnose failures
---

# Debug
""",
    )
    skills = SkillRegistry(workdir=tmp_path).discover()

    selection = SkillSelector().select("$debug-helper traceback here", skills)

    assert selection.skill is not None
    assert selection.skill.name == "debug-helper"
    assert selection.arguments == "traceback here"
    assert selection.explicit is True


def test_skill_selector_supports_explicit_slash_invocation(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "debug-helper",
        """---
name: debug-helper
description: Diagnose failures
---

# Debug
""",
    )
    skills = SkillRegistry(workdir=tmp_path).discover()

    selection = SkillSelector().select("/debug-helper traceback here", skills)

    assert selection.skill is not None
    assert selection.skill.name == "debug-helper"
    assert selection.arguments == "traceback here"
    assert selection.explicit is True


def test_skill_selector_respects_user_invocable_false(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "hidden-skill",
        """---
name: hidden-skill
description: Hidden skill
user-invocable: false
---

# Hidden
""",
    )
    skills = SkillRegistry(workdir=tmp_path).discover()

    selection = SkillSelector().select("$hidden-skill now", skills)

    assert selection.skill is None


def test_skill_selector_automatically_matches_keywords(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "debug-helper",
        """---
name: debug-helper
description: Diagnose stack trace failures
triggers: ["traceback", "exception"]
---

# Debug
""",
    )
    skills = SkillRegistry(workdir=tmp_path).discover()

    selection = SkillSelector().select("please inspect this traceback", skills)

    assert selection.skill is not None
    assert selection.skill.name == "debug-helper"
    assert selection.explicit is False


def test_skill_selector_respects_disable_model_invocation(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "debug-helper",
        """---
name: debug-helper
description: Diagnose stack trace failures
triggers: ["traceback"]
disable-model-invocation: true
---

# Debug
""",
    )
    skills = SkillRegistry(workdir=tmp_path).discover()

    selection = SkillSelector().select("please inspect this traceback", skills)

    assert selection.skill is None


def test_prompt_composer_orders_project_and_skill_context(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("Project rule: keep CLI simple.", encoding="utf-8")
    _write_skill(
        tmp_path,
        "debug-helper",
        """---
name: debug-helper
description: Diagnose failures
allowed-tools: read
argument-hint: "<error>"
---

# Debug

Use args: $ARGUMENTS
Skill dir: ${TINY_CLAW_SKILL_DIR}
""",
    )

    context = PromptComposer().compose(
        prompt="$debug-helper broken test",
        workdir=tmp_path,
        memories=("last_prompt: hello",),
    )

    assert [message.role.value for message in context.messages] == [
        "system",
        "system",
        "system",
        "system",
        "system",
        "user",
    ]
    assert "Core rules" in context.messages[0].content
    assert "Project rule: keep CLI simple." in context.messages[1].content
    assert "Available project skills" in context.messages[2].content
    assert "Active skill (explicit): debug-helper" in context.messages[3].content
    assert "Use args: broken test" in context.messages[3].content
    assert str(tmp_path / ".claw/skills/debug-helper") in context.messages[3].content
    assert "Recent memory" in context.messages[4].content
    assert context.messages[-1].content == "$debug-helper broken test"
    assert context.allowed_tools == ("read",)


def test_prompt_composer_does_not_limit_tools_when_skill_omits_allowed_tools(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "git-workflow",
        """---
name: git-workflow
description: Git workflow
---

# Git Workflow
""",
    )

    context = PromptComposer().compose(
        prompt="$git-workflow commit changes",
        workdir=tmp_path,
    )

    assert [skill.name for skill in context.selected_skills] == ["git-workflow"]
    assert context.allowed_tools is None


def test_context_builder_keeps_compatible_entrypoint(tmp_path) -> None:
    context = ContextBuilder(workdir=tmp_path).build(prompt="hello", memories=())

    assert context.messages[-1].content == "hello"
    assert context.selected_skills == ()


def _write_skill(tmp_path, name: str, content: str) -> None:
    skill_dir = tmp_path / ".claw" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
