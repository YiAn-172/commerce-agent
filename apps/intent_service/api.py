from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import Field

from apps.api.settings import get_settings
from apps.intent_service.runtime import IntentRuntime
from packages.contracts.base import StrictModel
from packages.contracts.intent import IntentPrediction


class PredictRequest(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    previous_user_text: str | None = Field(default=None, min_length=1, max_length=500)


class BatchPredictRequest(StrictModel):
    items: list[PredictRequest] = Field(min_length=1, max_length=128)


class BatchPredictResponse(StrictModel):
    predictions: list[IntentPrediction]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.intent_runtime = None
    app.state.load_error = None
    try:
        runtime = IntentRuntime(Path(get_settings().model_path))
        await run_in_threadpool(runtime.warmup, 10)
        app.state.intent_runtime = runtime
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        app.state.load_error = str(exc)
    yield


app = FastAPI(
    title="CommerceAgent Intent Service",
    version="0.2.0",
    lifespan=lifespan,
)


def require_runtime(request: Request) -> IntentRuntime:
    runtime: IntentRuntime | None = request.app.state.intent_runtime
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "intent model is not ready",
                "load_error": request.app.state.load_error,
            },
        )
    return runtime


@app.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health", tags=["health"])
async def health(request: Request) -> dict[str, str]:
    runtime = require_runtime(request)
    return {"status": "ready", "model_version": runtime.metadata.model_version}


@app.get("/health/ready", tags=["health"])
async def ready(request: Request) -> dict[str, str]:
    runtime = require_runtime(request)
    return {
        "status": "ready",
        "model_path": str(runtime.model_root),
        "model_version": runtime.metadata.model_version,
    }


@app.post("/predict", response_model=IntentPrediction, tags=["inference"])
async def predict(payload: PredictRequest, request: Request) -> IntentPrediction:
    runtime = require_runtime(request)
    return await run_in_threadpool(
        runtime.predict,
        payload.text,
        payload.previous_user_text,
    )


@app.post("/batch_predict", response_model=BatchPredictResponse, tags=["inference"])
async def batch_predict(payload: BatchPredictRequest, request: Request) -> BatchPredictResponse:
    runtime = require_runtime(request)
    predictions = await run_in_threadpool(
        runtime.predict_batch,
        [item.text for item in payload.items],
        [item.previous_user_text for item in payload.items],
    )
    return BatchPredictResponse(predictions=predictions)
