from fastapi import FastAPI

app = FastAPI(title="x-aout-agent-api")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
