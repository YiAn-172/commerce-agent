from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.contracts.intent import IntentLabel


class LicenseStatus(StrEnum):
    APPROVED = "approved_for_experiment"
    REVIEW_REQUIRED = "review_required"
    REJECTED = "rejected"


class SourceKind(StrEnum):
    HUGGINGFACE = "huggingface_dataset"
    GIT = "git_repository"
    GENERATED = "generated"


class DataSourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=2)
    kind: SourceKind
    repo_id: str | None = None
    url: str
    requested_revision: str
    resolved_revision: str = Field(min_length=7)
    downloaded_at: str
    local_path: str
    declared_license: str
    upstream_source: str
    allowed_use: str
    redistribution_allowed: bool
    attribution_required: bool
    license_status: LicenseStatus
    approved_record_sources: list[str] = Field(default_factory=list)
    handler: str
    notes: str
    file_count: int = Field(ge=1)
    total_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_huggingface_repo(self) -> DataSourceRecord:
        if self.kind == SourceKind.HUGGINGFACE and not self.repo_id:
            raise ValueError("repo_id is required for Hugging Face datasets")
        return self


class CandidateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str
    text_original: str = Field(min_length=1)
    text_zh: str | None = None
    source_id: str
    source_revision: str
    source_row_id: str
    source_language: str
    source_intent: str
    target_intent: IntentLabel | None = None
    translation_model: str | None = None
    generator_model: str | None = None
    prompt_version: str | None = None
    generation_seed: int | None = None
    generated_at: str | None = None
    template_family: str | None = None
    source_dialogue_id: str
    semantic_cluster_id: str | None = None
    license_status: LicenseStatus
    pii_status: str = "pending"
    quality_status: str = "pending"
    provenance: str

    @property
    def group_key(self) -> str:
        values = (
            self.source_dialogue_id,
            self.template_family or "none",
            self.semantic_cluster_id or "none",
        )
        return "|".join(values)
