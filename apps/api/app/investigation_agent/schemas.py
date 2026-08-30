"""Strict contracts between ADK reasoning, persistence, and browser execution."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ShortText = Annotated[str, Field(min_length=1, max_length=500)]
LongText = Annotated[str, Field(min_length=1, max_length=5_000)]
Selector = Annotated[str, Field(min_length=1, max_length=500)]
Value = Annotated[str, Field(max_length=2_000)]
FixContent = Annotated[str, Field(max_length=50_000)]


def _relative_path(value: str) -> str:
    value = value.strip()
    if not value.startswith("/") or value.startswith("//"):
        raise ValueError("Browser navigation must use an origin-relative path.")
    if any(character in value for character in ("\r", "\n", "\0")):
        raise ValueError("Browser paths cannot contain control characters.")
    return value


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepositoryFinding(StrictSchema):
    path: Annotated[str, Field(min_length=1, max_length=1_000)]
    reason: LongText
    observation: LongText


class DuplicateCandidate(StrictSchema):
    issue_number: Annotated[int, Field(gt=0)]
    title: ShortText
    url: Annotated[str, Field(min_length=1, max_length=2_000)]
    similarity: Literal["low", "medium", "high"]
    reason: LongText


class ProposedFileChange(StrictSchema):
    path: Annotated[str, Field(min_length=1, max_length=1_000)]
    original_content: FixContent
    updated_content: FixContent
    explanation: LongText

    @field_validator("original_content", "updated_content")
    @classmethod
    def reject_binary_content(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("Fix proposal content must be UTF-8 text.")
        return value

    @model_validator(mode="after")
    def require_a_change(self) -> "ProposedFileChange":
        if self.original_content == self.updated_content:
            raise ValueError("A proposed file must contain a change.")
        return self


class FixProposal(StrictSchema):
    summary: LongText
    files: list[ProposedFileChange] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def require_unique_paths(self) -> "FixProposal":
        paths = [change.path for change in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("A fix proposal cannot change a file more than once.")
        return self


class GotoAction(StrictSchema):
    type: Literal["goto"]
    path: Annotated[str, Field(min_length=1, max_length=2_000)]

    _validate_path = field_validator("path")(_relative_path)


class ClickAction(StrictSchema):
    type: Literal["click"]
    selector: Selector


class FillAction(StrictSchema):
    type: Literal["fill"]
    selector: Selector
    value: Value


class PressAction(StrictSchema):
    type: Literal["press"]
    selector: Selector
    key: Annotated[str, Field(min_length=1, max_length=100)]


class WaitForAction(StrictSchema):
    type: Literal["wait_for"]
    selector: Selector


class ExpectTextAction(StrictSchema):
    type: Literal["expect_text"]
    selector: Selector
    value: Value


class ExpectVisibleAction(StrictSchema):
    type: Literal["expect_visible"]
    selector: Selector


class ExpectUrlAction(StrictSchema):
    type: Literal["expect_url"]
    value: Annotated[str, Field(min_length=1, max_length=2_000)]

    _validate_value = field_validator("value")(_relative_path)


BrowserAction = Annotated[
    GotoAction
    | ClickAction
    | FillAction
    | PressAction
    | WaitForAction
    | ExpectTextAction
    | ExpectVisibleAction
    | ExpectUrlAction,
    Field(discriminator="type"),
]


class BrowserTestPlan(StrictSchema):
    """Validated browser DSL; URLs are paths under the persisted app origin."""

    name: ShortText
    start_path: Annotated[str, Field(min_length=1, max_length=2_000)] = "/"
    actions: list[BrowserAction] = Field(min_length=1, max_length=30)

    _validate_start_path = field_validator("start_path")(_relative_path)


class AgentInvestigationResult(StrictSchema):
    repository_findings: list[RepositoryFinding] = Field(max_length=30)
    duplicate_candidates: list[DuplicateCandidate] = Field(max_length=10)
    reproduction_plan: BrowserTestPlan | None = None
    cannot_reproduce_reason: Annotated[str, Field(max_length=5_000)] | None = None
    fix_proposal: FixProposal | None = None
    cannot_propose_fix_reason: Annotated[str, Field(max_length=2_000)] | None = None

    @model_validator(mode="after")
    def require_plan_or_reason(self) -> "AgentInvestigationResult":
        if self.reproduction_plan is None and not self.cannot_reproduce_reason:
            raise ValueError("A missing browser plan requires a reason.")
        if self.fix_proposal is None and (
            not self.cannot_propose_fix_reason
            or not self.cannot_propose_fix_reason.strip()
        ):
            raise ValueError("A missing fix proposal requires a reason.")
        if (
            self.fix_proposal is not None
            and self.cannot_propose_fix_reason is not None
        ):
            raise ValueError("A fix proposal cannot also include a no-fix reason.")
        return self


class BrowserExecutionResult(StrictSchema):
    status: Literal["reproduced", "not_reproduced", "blocked"]
    completed_actions: Annotated[int, Field(ge=0, le=30)]
    failed_action_index: Annotated[int, Field(ge=0, le=29)] | None
    expected: Annotated[str, Field(max_length=2_000)] | None
    actual: Annotated[str, Field(max_length=2_000)] | None
    summary: Annotated[str, Field(min_length=1, max_length=5_000)]
