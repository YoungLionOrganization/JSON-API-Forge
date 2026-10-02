from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class APIKeyCreate(APIModel):
    name: str = Field(min_length=1, max_length=128)
    roles: list[str] = Field(default_factory=list, max_length=64)
    permissions: list[str] = Field(default_factory=list, max_length=256)
    tenant_id: str | None = Field(default=None, max_length=128)
    rate_requests: int | None = Field(default=None, ge=1, le=10_000_000)
    rate_window_seconds: int | None = Field(default=None, ge=1, le=86_400)
    rate_burst: int | None = Field(default=None, ge=1, le=10_000_000)
    expires_at: datetime | None = None

    @field_validator("roles", "permissions")
    @classmethod
    def validate_items(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 192 for value in values):
            raise ValueError("role/permission entries must be 1..192 characters")
        return values


class JWTCreate(APIModel):
    subject: str = Field(min_length=1, max_length=256)
    roles: list[str] = Field(default_factory=list, max_length=64)
    permissions: list[str] = Field(default_factory=list, max_length=256)
    tenant_id: str | None = Field(default=None, max_length=128)
    exp_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 30)

    @field_validator("roles", "permissions")
    @classmethod
    def validate_items(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 192 for value in values):
            raise ValueError("role/permission entries must be 1..192 characters")
        return values
