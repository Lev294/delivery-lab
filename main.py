from fastapi import FastAPI

app = FastAPI()


@app.get("/hello/{name}")
def hello(name: str):
    return {"message": f"Hello, {name}!"}

@app.get("/delivery/estimate/{distance_km}")
def estimate(distance_km: int):
    estimated_minutes = 10 + 3 * distance_km
    return {"distance_km": distance_km,
            "estimated_minutes": estimated_minutes}