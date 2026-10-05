"""
Brew Bar · order-service
========================

The second service. It owns *orders* and asks menu-service about prices
over HTTP. This is the "service-to-service" half of the session.

Inside Kubernetes, MENU_URL is http://menu-service (the Service's DNS name).
Locally it defaults to http://localhost:8001.

Run locally (with menu-service already running on 8001):
    uvicorn app.main:app --reload --port 8002
"""

import os
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, Field

POD_NAME = socket.gethostname()
MENU_URL = os.getenv("MENU_URL", "http://localhost:8001")


# LESSON: create ONE http client per process and reuse it.
# It keeps connections open (pooling) and carries the timeout policy.
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.menu = httpx.AsyncClient(base_url=MENU_URL, timeout=httpx.Timeout(2.0))
    yield
    await app.state.menu.aclose()


app = FastAPI(
    title="Brew Bar · order-service",
    version="1.0.0",
    description=f"Places orders. Looks up prices by calling **menu-service** at `{MENU_URL}`.",
    lifespan=lifespan,
)


class OrderIn(BaseModel):
    customer: str = Field(min_length=1, max_length=30, examples=["Asha"])
    item_id: int = Field(ge=1, examples=[2])
    quantity: int = Field(default=1, ge=1, le=10)


class Order(BaseModel):
    id: int
    customer: str
    item_id: int
    item_name: str
    quantity: int
    total_cents: int
    created_at: datetime
    priced_by_pod: str = Field(description="Which menu-service pod answered the price lookup")


ORDERS: dict[int, Order] = {}


@app.middleware("http")
async def add_trace_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Served-By"] = POD_NAME
    return response


@app.get("/", summary="Service info")
def root():
    return {"service": "order-service", "served_by": POD_NAME, "menu_url": MENU_URL, "docs": "/docs"}


# LESSON: one service calling another. Every way the downstream call can go
# wrong gets mapped to an honest status code for OUR caller:
#   menu says 404          → 422  (your request names an item that doesn't exist)
#   item not available     → 409  (conflicts with current state)
#   menu down / timed out  → 503 / 504 (not your fault, try again later)
@app.post("/orders", response_model=Order, status_code=status.HTTP_201_CREATED, summary="Place an order")
async def create_order(body: OrderIn, request: Request, response: Response):
    menu: httpx.AsyncClient = request.app.state.menu
    try:
        r = await menu.get(f"/menu/{body.item_id}")
    except httpx.TimeoutException:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail="menu-service timed out")
    except httpx.RequestError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"menu-service unreachable: {exc!r}")

    if r.status_code == 404:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"No menu item with id {body.item_id}")
    if r.status_code != 200:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"menu-service answered {r.status_code}")

    item = r.json()
    if not item["available"]:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{item['name']} is sold out")

    order = Order(
        id=len(ORDERS) + 1,
        customer=body.customer,
        item_id=item["id"],
        item_name=item["name"],
        quantity=body.quantity,
        total_cents=item["price_cents"] * body.quantity,
        created_at=datetime.now(timezone.utc),
        priced_by_pod=r.headers.get("X-Served-By", "unknown"),
    )
    ORDERS[order.id] = order
    response.headers["Location"] = f"/orders/{order.id}"
    return order


@app.get("/orders", response_model=list[Order], summary="List orders")
def list_orders():
    return list(ORDERS.values())


@app.get("/orders/{order_id}", response_model=Order, summary="Get one order")
def get_order(order_id: Annotated[int, Path(ge=1)]):
    if order_id not in ORDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Order {order_id} not found")
    return ORDERS[order_id]


@app.get("/healthz", summary="Liveness probe")
def healthz():
    return {"status": "alive", "pod": POD_NAME}


# Readiness checks only THIS service. If it also checked menu-service, one slow
# menu pod could mark every order pod unready and take the whole shop offline.
@app.get("/readyz", summary="Readiness probe")
def readyz():
    return {"status": "ready", "pod": POD_NAME}
