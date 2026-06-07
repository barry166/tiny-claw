from __future__ import annotations

from tiny_claw._internal.context.plan import (
    PlanMarkdownParser,
    PlanResponseParser,
    plan_step_status,
)


def test_plan_markdown_parser_finds_and_marks_next_open_item() -> None:
    todo_text = "# TODO.md\n\n- [x] TC-001 Done\n- [ ] TC-002 Continue\n"

    item = PlanMarkdownParser.next_open_item(todo_text)
    updated = PlanMarkdownParser.mark_done(todo_text, "TC-002")

    assert item is not None
    assert item.id == "TC-002"
    assert item.text == "Continue"
    assert "- [x] TC-002 Continue" in updated


def test_plan_response_parser_extracts_documents_and_adds_missing_todo_ids() -> None:
    plan_text, todo_text = PlanResponseParser.parse_create_response(
        response_text=(
            "<PLAN_MD># PLAN.md\n\n## 目标理解\n\nBuild.</PLAN_MD>"
            "<TODO_MD># TODO.md\n\n- [ ] Create files</TODO_MD>"
        ),
        prompt="build",
    )

    assert plan_text.startswith("# PLAN.md")
    assert "- [ ] TC-001 Create files" in todo_text


def test_plan_step_status_parses_completion_marker() -> None:
    assert plan_step_status("ok\nPLAN_STEP_STATUS: completed") == "completed"
    assert plan_step_status("PLAN_STEP_STATUS: blocked") == "blocked"
    assert plan_step_status("plain text") is None
