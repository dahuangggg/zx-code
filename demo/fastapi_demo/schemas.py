from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    now: datetime


class EchoIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class EchoOut(BaseModel):
    echoed: str


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price: float = Field(gt=0)
    tags: list[str] = Field(default_factory=list, max_length=20)
    in_stock: bool = True


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price: float | None = Field(default=None, gt=0)
    tags: list[str] | None = Field(default=None, max_length=20)
    in_stock: bool | None = None


class Item(ItemIn):
    id: int


class ItemListResponse(BaseModel):
    total: int
    items: list[Item]


class SummaryResponse(BaseModel):
    total_items: int
    total_value: float


class WhoAmIResponse(BaseModel):
    user_agent: str | None
    request_id: str | None
