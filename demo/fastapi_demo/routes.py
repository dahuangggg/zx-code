from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .deps import require_api_key
from .schemas import (
    EchoIn,
    EchoOut,
    HealthResponse,
    Item,
    ItemIn,
    ItemListResponse,
    ItemUpdate,
    SummaryResponse,
    WhoAmIResponse,
)
from .utils import utc_now

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(now=utc_now())


@router.post("/echo", response_model=EchoOut)
def echo(payload: EchoIn) -> EchoOut:
    return EchoOut(echoed=payload.message)


@dataclass
class _ItemStore:
    db: dict[int, Item]
    next_id: int = 1


_store = _ItemStore(db={})


@router.post("/items", response_model=Item)
def create_item(payload: ItemIn) -> Item:
    item = Item(id=_store.next_id, **payload.model_dump())
    _store.db[item.id] = item
    _store.next_id += 1
    return item


@router.get("/items/{item_id}", response_model=Item)
def get_item(item_id: int) -> Item:
    item = _store.db.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.get("/items", response_model=ItemListResponse)
def list_items(
    q: str | None = Query(default=None, description="Name contains (case-insensitive)"),
    tag: str | None = Query(default=None, description="Has a tag (exact match)"),
    in_stock: bool | None = Query(default=None, description="Filter by stock status"),
    sort: Literal["id", "price", "name"] = Query(default="id"),
    order: Literal["asc", "desc"] = Query(default="asc"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> ItemListResponse:
    items = list(_store.db.values())

    if q:
        ql = q.lower()
        items = [it for it in items if ql in it.name.lower()]
    if tag:
        items = [it for it in items if tag in it.tags]
    if in_stock is not None:
        items = [it for it in items if it.in_stock == in_stock]

    reverse = order == "desc"
    if sort == "id":
        items.sort(key=lambda x: x.id, reverse=reverse)
    elif sort == "price":
        items.sort(key=lambda x: x.price, reverse=reverse)
    else:
        items.sort(key=lambda x: x.name.lower(), reverse=reverse)

    total = len(items)
    items = items[offset : offset + limit]
    return ItemListResponse(total=total, items=items)


@router.patch("/items/{item_id}", response_model=Item)
def update_item(item_id: int, payload: ItemUpdate) -> Item:
    item = _store.db.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    data = payload.model_dump(exclude_unset=True)
    updated = item.model_copy(update=data)
    _store.db[item_id] = updated
    return updated


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int):
    if item_id not in _store.db:
        raise HTTPException(status_code=404, detail="Item not found")
    del _store.db[item_id]


@router.get("/admin/summary", response_model=SummaryResponse, dependencies=[Depends(require_api_key)])
def admin_summary() -> SummaryResponse:
    items = list(_store.db.values())
    total_value = sum(it.price for it in items)
    return SummaryResponse(total_items=len(items), total_value=total_value)


@router.get("/whoami", response_model=WhoAmIResponse)
def whoami(request: Request) -> WhoAmIResponse:
    return WhoAmIResponse(
        user_agent=request.headers.get("user-agent"),
        request_id=request.headers.get("x-request-id"),
    )
