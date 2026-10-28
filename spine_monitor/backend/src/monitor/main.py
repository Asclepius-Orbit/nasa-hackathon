from fastapi import FastAPI

app = FastAPI(title="Spine Monitor")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
