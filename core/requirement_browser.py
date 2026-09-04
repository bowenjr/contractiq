"""Typed boundary between multipart browser forms and TASK-09 commands."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class RequirementBrowserCommand(BaseModel):
    """Allow only documented requirement, source, and navigation form fields."""

    model_config = ConfigDict(extra="forbid")

    bid_id: str
    title: str
    statement: str
    interpretation: str | None = None
    origin: str
    category: str
    significance: str
    lifecycle_stage: str = "BID"
    owner: str | None = None
    contributor: str | None = None
    reviewer: str | None = None
    due_date: str | None = None
    disposition: str = "UNASSESSED"
    response_text: str | None = None
    work_state: str = "OPEN"
    source_document_version_id: str | None = None
    source_clause: str | None = None
    source_page_start: str | None = None
    source_page_end: str | None = None
    source_locator_note: str | None = None
    source_excerpt: str | None = None
    new_source_title: str | None = None
    new_source_version_label: str | None = None
    new_source_file: object | None = None
    source_operation_token: str | None = None
    form_state: str | None = None

    def requirement_payload(self) -> dict[str, Any]:
        """Return only fields accepted by the strict authoritative create model."""
        names = (
            "bid_id",
            "title",
            "statement",
            "interpretation",
            "origin",
            "category",
            "significance",
            "lifecycle_stage",
            "owner",
            "contributor",
            "reviewer",
            "due_date",
            "disposition",
            "response_text",
            "work_state",
            "source_document_version_id",
            "source_clause",
            "source_page_start",
            "source_page_end",
            "source_locator_note",
            "source_excerpt",
        )
        payload: dict[str, Any] = {}
        for name in names:
            value = getattr(self, name)
            if value in (None, ""):
                continue
            payload[name] = (
                int(value) if name in {"source_page_start", "source_page_end"} else value
            )
        return payload

    def retained_values(self) -> dict[str, str]:
        """Return safe textual values for one retained server-rendered form."""
        return {
            name: str(value)
            for name, value in self.model_dump(exclude={"new_source_file"}).items()
            if value is not None
        }
