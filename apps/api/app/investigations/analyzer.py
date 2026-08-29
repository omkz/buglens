"""Structured bug-analysis contract and evidence preparation service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .evidence_storage import EvidenceStorage, EvidenceStorageError, RecordingSource
from .repository import PersistedEvidence, PersistedInvestigation

AnalysisText = Annotated[str, Field(max_length=10_000)]
AnalysisListItem = Annotated[str, Field(max_length=2_000)]


class BugAnalysis(BaseModel):
    """Evidence-grounded, structured understanding returned by the analyzer."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    summary: AnalysisText = Field(
        description="A concise summary supported by the supplied evidence."
    )
    observed_behavior: AnalysisText = Field(
        description="What visibly or explicitly happened in the evidence."
    )
    expected_behavior: AnalysisText | None = Field(
        description="Expected behavior only when it can reasonably be inferred."
    )
    reproduction_steps: list[AnalysisListItem] = Field(
        max_length=50,
        description="Ordered evidence-supported steps; empty when not established.",
    )
    error_signals: list[AnalysisListItem] = Field(
        max_length=50,
        description="Visible or logged errors, warnings, and failure signals.",
    )
    suspected_components: list[AnalysisListItem] = Field(
        max_length=50,
        description="Likely affected UI or application areas, not source-code causes.",
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description="Confidence based only on the completeness of supplied evidence."
    )
    needs_more_information: bool = Field(
        description="True when evidence is insufficient for a useful understanding."
    )
    missing_information: list[AnalysisListItem] = Field(
        max_length=50,
        description="Specific additional evidence needed; empty when none is needed.",
    )


@dataclass(frozen=True)
class RecordingEvidence:
    source: RecordingSource
    mime_type: str


@dataclass(frozen=True)
class AnalysisInput:
    title: str
    description: str | None
    logs: list[str]
    recordings: list[RecordingEvidence]


class BugAnalyzer(Protocol):
    @property
    def model_name(self) -> str: ...

    async def analyze(self, analysis_input: AnalysisInput) -> BugAnalysis: ...


class AnalyzerConfigurationError(RuntimeError):
    """Raised when analysis cannot run because backend configuration is absent."""


class AnalyzerProviderError(RuntimeError):
    """Raised for safe, provider-facing analysis failures."""


class AnalyzerEvidenceError(RuntimeError):
    """Raised when trusted recording evidence cannot be prepared."""


class InvestigationAnalyzerService:
    """Resolve trusted evidence and delegate understanding to a BugAnalyzer."""

    def __init__(self, analyzer: BugAnalyzer, storage: EvidenceStorage):
        self.analyzer = analyzer
        self.storage = storage

    @property
    def model_name(self) -> str:
        return self.analyzer.model_name

    async def analyze(
        self,
        investigation: PersistedInvestigation,
        evidence: list[PersistedEvidence],
    ) -> BugAnalysis:
        logs: list[str] = []
        recordings: list[RecordingEvidence] = []
        try:
            for item in evidence:
                if item.kind == "logs" and item.text_content is not None:
                    logs.append(item.text_content)
                elif item.kind == "recording":
                    if item.storage_key is None:
                        raise AnalyzerEvidenceError(
                            "Recording metadata has no storage key."
                        )
                    source = await self.storage.resolve_recording(item.storage_key)
                    mime_type = (item.mime_type or "video/webm").split(";", 1)[0]
                    recordings.append(
                        RecordingEvidence(source=source, mime_type=mime_type)
                    )

            return await self.analyzer.analyze(
                AnalysisInput(
                    title=investigation.title,
                    description=investigation.description,
                    logs=logs,
                    recordings=recordings,
                )
            )
        except EvidenceStorageError as exc:
            raise AnalyzerEvidenceError(
                "Recording evidence is unavailable."
            ) from exc
