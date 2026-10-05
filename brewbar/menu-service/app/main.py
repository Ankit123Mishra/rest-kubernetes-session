"""
Brew Bar · menu-service
=======================

A toy REST API for teaching. It manages one resource, a coffee-shop *menu item*,
and every endpoint below demonstrates one REST idea. Look for the "LESSON"
comment above each route.

Run locally:
    uvicorn app.main:app --reload --port 8001
Then open:
    http://localhost:8001/docs      (Swagger UI, generated from this file)
    http://localhost:8001/          (a guided tour of the endpoints)
"""

import os
import socket
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

POD_NAME = socket.gethostname()          # inside Kubernetes this is the pod name
API_KEY = os.getenv("API_KEY", "barista-secret")

app = FastAPI(
    title="Brew Bar · menu-service",
    version="1.0.0",
    description=(
        "A teaching API. Each endpoint shows one REST concept: resources, "
        "HTTP methods, status codes, validation, pagination and idempotency.\n\n"
        "Write endpoints need the header `X-API-Key: barista-secret` "
        "(click **Authorize** above)."
    ),
    openapi_tags=[
        {"name": "tour", "description": "Start here."},
        {"name": "menu", "description": "CRUD on the `/menu` collection."},
        {"name": "ops", "description": "Endpoints Kubernetes talks to."},
    ],
)


# ---------------------------------------------------------------------------
# 1. MODELS: the shape of the resource
#    Pydantic validates every request body. Bad input never reaches your code;
#    FastAPI answers 422 Unprocessable Entity with a precise error message.
# ---------------------------------------------------------------------------

class Category(str, Enum):
    espresso = "espresso"
    brew = "brew"
    tea = "tea"
    pastry = "pastry"


class MenuItemIn(BaseModel):
    """What a client sends to create or fully replace an item."""
    name: str = Field(min_length=2, max_length=40, examples=["Flat White"])
    category: Category = Field(examples=["espresso"])
    price_cents: int = Field(gt=0, le=2000, description="Money as integer cents, never floats", examples=[450])
    available: bool = True


class MenuItemPatch(BaseModel):
    """PATCH body: every field optional, only the ones you send are changed."""
    name: str | None = Field(default=None, min_length=2, max_length=40)
    category: Category | None = None
    price_cents: int | None = Field(default=None, gt=0, le=2000)
    available: bool | None = None


class MenuItem(MenuItemIn):
    """What the server returns: the input plus server-owned fields."""
    id: int
    updated_at: datetime


class Page(BaseModel):
    items: list[MenuItem]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# 2. STORAGE: an in-memory dict
#    Deliberately naive. With 2+ replicas each pod has its OWN dict, which is
#    the live demo for "why services should be stateless" (see README).
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


DB: dict[int, MenuItem] = {}
_next_id = 1


def _seed() -> None:
    global _next_id
    for name, cat, price in [
        ("Espresso", "espresso", 300),
        ("Flat White", "espresso", 450),
        ("Cortado", "espresso", 420),
        ("Pour Over", "brew", 500),
        ("Cold Brew", "brew", 480),
        ("Sencha", "tea", 380),
        ("Croissant", "pastry", 350),
    ]:
        DB[_next_id] = MenuItem(id=_next_id, name=name, category=cat, price_cents=price, updated_at=_now())
        _next_id += 1


_seed()


def get_item_or_404(item_id: Annotated[int, Path(ge=1, description="Menu item id")]) -> MenuItem:
    """A dependency: reused by every route that addresses ONE item."""
    item = DB.get(item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Menu item {item_id} not found")
    return item


# ---------------------------------------------------------------------------
# 3. AUTH (tiny): a dependency that guards write endpoints
#    401 = "who are you?"  vs  403 = "I know who you are, and no."
# ---------------------------------------------------------------------------

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_barista(key: Annotated[str | None, Depends(api_key_header)]) -> None:
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key header")
    if key != API_KEY:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Wrong API key")


# ---------------------------------------------------------------------------
# 4. MIDDLEWARE: runs around every request
#    Adds X-Served-By so you can SEE Kubernetes load-balancing across pods.
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_trace_headers(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Served-By"] = POD_NAME
    response.headers["X-Process-Time-ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
    return response


# ===========================================================================
# ROUTES
# ===========================================================================

@app.get("/", tags=["tour"], summary="Guided tour of this API")
def tour():
    """LESSON: an API can describe itself. Hit this first in the demo."""
    return {
        "service": "menu-service",
        "served_by": POD_NAME,
        "docs": "/docs",
        "lessons": [
            {"method": "GET",    "path": "/menu",            "concept": "Read a collection; filter + paginate with query params", "safe": True,  "idempotent": True},
            {"method": "GET",    "path": "/menu/{id}",       "concept": "Read one resource; 404 when it doesn't exist",          "safe": True,  "idempotent": True},
            {"method": "POST",   "path": "/menu",            "concept": "Create; 201 + Location header; 409 on duplicate",       "safe": False, "idempotent": False},
            {"method": "PUT",    "path": "/menu/{id}",       "concept": "Replace the whole resource",                            "safe": False, "idempotent": True},
            {"method": "PATCH",  "path": "/menu/{id}",       "concept": "Change only the fields you send",                       "safe": False, "idempotent": False},
            {"method": "DELETE", "path": "/menu/{id}",       "concept": "Remove; 204 No Content",                                "safe": False, "idempotent": True},
            {"method": "GET",    "path": "/healthz",         "concept": "Liveness probe: is the process alive?",                 "safe": True,  "idempotent": True},
            {"method": "GET",    "path": "/readyz",          "concept": "Readiness probe: should I get traffic?",                "safe": True,  "idempotent": True},
        ],
    }


# LESSON: GET on a collection. Query parameters filter and paginate;
# they never identify a resource (that's the path's job).
@app.get("/menu", response_model=Page, tags=["menu"], summary="List menu items")
def list_items(
    category: Category | None = None,
    available: bool | None = None,
    max_price_cents: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = list(DB.values())
    if category is not None:
        items = [i for i in items if i.category == category]
    if available is not None:
        items = [i for i in items if i.available == available]
    if max_price_cents is not None:
        items = [i for i in items if i.price_cents <= max_price_cents]
    return Page(items=items[offset : offset + limit], total=len(items), limit=limit, offset=offset)


# LESSON: GET on one resource. The id lives in the path. Missing → 404.
@app.get("/menu/{item_id}", response_model=MenuItem, tags=["menu"], summary="Get one item")
def get_item(item: Annotated[MenuItem, Depends(get_item_or_404)]):
    return item


# LESSON: POST creates a NEW resource whose id the SERVER chooses.
# Correct reply: 201 Created + a Location header pointing at the new thing.
# Not idempotent: sending it twice would make two items, so we reject duplicates with 409.
@app.post(
    "/menu",
    response_model=MenuItem,
    status_code=status.HTTP_201_CREATED,
    tags=["menu"],
    summary="Create an item",
    dependencies=[Depends(require_barista)],
)
def create_item(body: MenuItemIn, response: Response):
    global _next_id
    if any(i.name.lower() == body.name.lower() for i in DB.values()):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"'{body.name}' is already on the menu")
    item = MenuItem(id=_next_id, updated_at=_now(), **body.model_dump())
    DB[item.id] = item
    _next_id += 1
    response.headers["Location"] = f"/menu/{item.id}"
    return item


# LESSON: PUT replaces the WHOLE resource. Send the same PUT ten times and
# the result is the same as sending it once: that is idempotency.
@app.put(
    "/menu/{item_id}",
    response_model=MenuItem,
    tags=["menu"],
    summary="Replace an item",
    dependencies=[Depends(require_barista)],
)
def replace_item(body: MenuItemIn, item: Annotated[MenuItem, Depends(get_item_or_404)]):
    updated = MenuItem(id=item.id, updated_at=_now(), **body.model_dump())
    DB[item.id] = updated
    return updated


# LESSON: PATCH changes only the fields present in the body.
# exclude_unset=True is the trick: it tells "not sent" apart from "sent as null".
#
# LESSON: path vs query vs body vs header, all in one request:
#   PATCH /menu/7?dry_run=true   X-API-Key: ...   {"price_cents": 400}
#     path   item_id  → WHICH resource
#     query  dry_run  → HOW to process it (optional switch, default False)
#     body   changes  → WHAT you are sending
#     header api key  → WHO is asking
@app.patch(
    "/menu/{item_id}",
    response_model=MenuItem,
    tags=["menu"],
    summary="Partially update an item",
    dependencies=[Depends(require_barista)],
)
def patch_item(
    body: MenuItemPatch,
    item: Annotated[MenuItem, Depends(get_item_or_404)],
    dry_run: Annotated[bool, Query(description="Preview the result without saving")] = False,
):
    changes = body.model_dump(exclude_unset=True)
    updated = item.model_copy(update={**changes, "updated_at": _now()})
    if not dry_run:
        DB[item.id] = updated
    return updated


# LESSON: DELETE answers 204 No Content (success, empty body).
# A second DELETE gets 404, but the server's state is the same: still idempotent.
@app.delete(
    "/menu/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["menu"],
    summary="Delete an item",
    dependencies=[Depends(require_barista)],
)
def delete_item(item: Annotated[MenuItem, Depends(get_item_or_404)]):
    del DB[item.id]


# ---------------------------------------------------------------------------
# OPS: endpoints for Kubernetes, not for humans
# ---------------------------------------------------------------------------

_ready = True


# LESSON: liveness. If this fails, Kubernetes RESTARTS the container.
@app.get("/healthz", tags=["ops"], summary="Liveness probe")
def healthz():
    return {"status": "alive", "pod": POD_NAME}


# LESSON: readiness. If this fails, Kubernetes stops SENDING TRAFFIC to the pod
# (removes it from the Service endpoints) but does not restart it.
@app.get("/readyz", tags=["ops"], summary="Readiness probe")
def readyz(response: Response):
    if not _ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "draining", "pod": POD_NAME}
    return {"status": "ready", "pod": POD_NAME}


# Demo helper: flip readiness, then watch `kubectl get endpoints menu-service -w`.
@app.post("/admin/toggle-ready", tags=["ops"], summary="Flip readiness (demo only)",
          dependencies=[Depends(require_barista)])
def toggle_ready():
    global _ready
    _ready = not _ready
    return {"ready": _ready, "pod": POD_NAME}


@app.get("/whoami", tags=["ops"], summary="Which pod answered?")
def whoami():
    return {"pod": POD_NAME, "items_in_my_memory": len(DB)}
