"""Operation request/outcome/state-machine 的稳定协议。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.models import ToolResult

OperationStatus = Literal["started", "committed", "failed", "unknown"]
SideEffect = Literal["none", "read", "write", "external"]


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    session_id: str
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    capability: str | None = None
    side_effect: SideEffect = "none"


class OperationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["committed", "failed", "unknown"]
    result: ToolResult | None = None
    error_code: str | None = None
    message: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_status_payload(self) -> "OperationOutcome":
        if self.status == "committed" and self.result is None:
            raise ValueError("committed outcome requires a result")
        if self.status == "failed" and not (self.error_code or self.result):
            raise ValueError("failed outcome requires error_code or result")
        return self


class OperationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    status: OperationStatus
    request: OperationRequest
    started_at: datetime
    outcome: OperationOutcome | None = None


class JournalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation_id: str
    status: OperationStatus
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request: OperationRequest | None = None
    outcome: OperationOutcome | None = None

    @model_validator(mode="after")
    def validate_transition_payload(self) -> "JournalEvent":
        if self.status == "started":
            if self.request is None or self.outcome is not None:
                raise ValueError("started event requires only request")
        elif self.request is not None or self.outcome is None:
            raise ValueError("terminal event requires only outcome")
        elif self.outcome.status != self.status:
            raise ValueError("event and outcome statuses must match")
        return self
