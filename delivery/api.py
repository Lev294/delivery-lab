import asyncio
import json
import logging
import os
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import grpc
import psycopg
from fastapi import APIRouter, Depends, Header, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from psycopg_pool import PoolTimeout, TooManyRequests
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

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
    status: Literal["pending", "reserved", "completed", "rejected"]
    failure_reason: str | None
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
        conn.execute("SELECT 1 FROM orders LIMIT 1").fetchone()
    return {"status": "ready", "instance_id": INSTANCE_ID}


@router.post("/orders", response_model=OrderView, status_code=201, tags=["orders"])
def create(
    body: NewOrder,
    response: Response,
    pool: Pool,
    request: Request,
    idempotency_key: Annotated[UUID, Header()],
):
    order, created = orders.create_order(
        pool, request.app.state.inventory, idempotency_key, body.sku, body.quantity
    )
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
def inventory(sku: str, request: Request):
    return request.app.state.inventory.stock(sku)


def replica_pool(request: Request):
    if request.app.state.replica is None:
        raise orders.OrderError("replica_not_configured", 503)
    return request.app.state.replica


@router.get("/replica/orders/{order_id}", response_model=OrderView, tags=["replica"])
def replica_order(order_id: UUID, response: Response, pool=Depends(replica_pool)):
    response.headers["X-Read-Source"] = "replica"
    return orders.get_order(pool, order_id)


@router.get("/replica/info", tags=["replica"])
def replica_info(pool=Depends(replica_pool)):
    with pool.connection() as conn:
        return conn.execute("""SELECT pg_is_in_recovery() AS in_recovery,
            pg_last_wal_replay_lsn()::text AS replay_lsn,
            pg_last_xact_replay_timestamp() AS last_replay_time""").fetchone()


@router.get("/lab", include_in_schema=False)
def lab():
    return FileResponse(Path(__file__).parent / "lab.html")


@router.websocket("/ws/orders/{order_id}")
async def watch(websocket: WebSocket, order_id: UUID):
    await websocket.accept()
    previous = None
    try:
        while True:
            order = await run_in_threadpool(orders.get_order, websocket.app.state.pool, order_id)
            signature = (order["status"], order["updated_at"])
            if previous != signature:
                await websocket.send_json(
                    {
                        "type": "snapshot",
                        "instance_id": INSTANCE_ID,
                        "order": jsonable_encoder(order),
                    }
                )
                previous = signature
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=1)
                if message == "refresh":
                    previous = None
            except TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    except orders.OrderError as exc:
        await websocket.send_json({"type": "error", "error": exc.code})
        await websocket.close(code=1008)
    except (psycopg.Error, PoolTimeout, TooManyRequests):
        await websocket.send_json({"type": "error", "error": "database_unavailable"})
        await websocket.close(code=1013)


def configure_app(app):
    app.include_router(router)

    @app.exception_handler(grpc.RpcError)
    async def rpc_error(request, exc):
        if exc.code() == grpc.StatusCode.NOT_FOUND:
            return JSONResponse(status_code=404, content={"error": "unknown_sku"})
        status = 504 if exc.code() == grpc.StatusCode.DEADLINE_EXCEEDED else 503
        return JSONResponse(
            status_code=status,
            content={"error": "inventory_unavailable", "retry": "same_key_and_body"},
        )

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
