import json
import logging
import os
import socket
import time
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout, TooManyRequests
from pydantic import BaseModel, ConfigDict, Field

from delivery import orders

router = APIRouter()
logger = logging.getLogger("uvicorn.error")
INSTANCE_ID = os.getenv("INSTANCE_ID", socket.gethostname())


class NewOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    quantity: int = Field(strict=True, ge=1, le=10000)


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["completed"]


class OrderView(BaseModel):
    id: UUID
    sku: str
    quantity: int
    status: Literal["reserved", "completed"]
    created_at: datetime
    updated_at: datetime


def get_pool(request: Request):
    pool = request.app.state.pool
    if pool is None:
        raise orders.OrderError("database_not_configured", 503)
    return pool


Pool = Annotated[object, Depends(get_pool)]


@router.get("/health/live", tags=["health"])
def live():
    return {"status": "alive", "instance_id": INSTANCE_ID}


@router.get("/health/ready", tags=["health"])
def ready(pool: Pool):
    with pool.connection() as conn:
        conn.execute("SELECT 1 FROM inventory LIMIT 1").fetchone()
    return {"status": "ready", "instance_id": INSTANCE_ID}


@router.post("/orders", response_model=OrderView, status_code=201, tags=["orders"])
def create(
    body: NewOrder,
    response: Response,
    pool: Pool,
    idempotency_key: Annotated[UUID, Header()],
):
    order, created = orders.create_order(pool, idempotency_key, body.sku, body.quantity)
    response.status_code = 201 if created else 200
    response.headers["Location"] = f"/orders/{order['id']}"
    return order


@router.get("/orders/{order_id}", response_model=OrderView, tags=["orders"])
def read(order_id: UUID, pool: Pool):
    return orders.get_order(pool, order_id)


@router.patch("/orders/{order_id}/status", response_model=OrderView, tags=["orders"])
def change_status(order_id: UUID, body: StatusChange, pool: Pool):
    return orders.complete_order(pool, order_id)


@router.get("/inventory/{sku}", tags=["inventory"])
def inventory(sku: str, pool: Pool):
    return orders.get_inventory(pool, sku)


def configure_app(app):
    app.include_router(router)

    @app.exception_handler(orders.OrderError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code})

    async def database_error(request, exc):
        # Do not serialize connection strings, passwords or SQL into the response/log.
        logger.warning(json.dumps({"event": "database_error", "type": type(exc).__name__}))
        return JSONResponse(status_code=503, content={"error": "database_unavailable"})

    for error in (
        psycopg.OperationalError,
        psycopg.errors.QueryCanceled,
        psycopg.errors.LockNotAvailable,
        PoolTimeout,
        TooManyRequests,
    ):
        app.add_exception_handler(error, database_error)

    @app.middleware("http")
    async def log_request(request, call_next):
        request_id = str(uuid4())
        started = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Instance-ID"] = INSTANCE_ID
            return response
        finally:
            logger.info(
                json.dumps(
                    {
                        "event": "request",
                        "request_id": request_id,
                        "instance_id": INSTANCE_ID,
                        "method": request.method,
                        "path": request.url.path,
                        "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    }
                )
            )
