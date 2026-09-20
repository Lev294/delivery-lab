from typing import Annotated

from fastapi import FastAPI, Path

from delivery.api import configure_app
from delivery.db import lifespan

app = FastAPI(title="Delivery Lab", version="1.0.0", lifespan=lifespan)
configure_app(app)


@app.get("/hello/{name}")
def hello(name: str):
    return {"message": f"Hello, {name}!"}


@app.get("/delivery/estimate/{distance_km}")
def estimate(distance_km: Annotated[int, Path(ge=0, le=50)]):
    estimated_minutes = 10 + 3 * distance_km
    return {"distance_km": distance_km, "estimated_minutes": estimated_minutes}
