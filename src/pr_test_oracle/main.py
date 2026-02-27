"""FastAPI application for PR Test Oracle."""

import os
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from simple_logger.logger import get_logger

from pr_test_oracle.analyzer import _merge_settings, analyze_pr
from pr_test_oracle.config import Settings, get_settings
from pr_test_oracle.models import AnalyzeRequest, AnalyzeResponse

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))

app = FastAPI(
    title="PR Test Oracle",
    description="AI-powered service that analyzes GitHub PRs and recommends which tests to run",
    version="0.1.0",
)


@app.post("/analyze")
async def analyze(
    body: AnalyzeRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AnalyzeResponse:
    """Analyze a PR and return test recommendations.

    Fetches the PR diff, maps changed files to tests via static analysis,
    sends context to AI for analysis, and optionally posts a PR comment.
    """
    merged = _merge_settings(body, settings)
    try:
        return await analyze_pr(body, merged)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


def run() -> None:
    """Entry point for the CLI."""
    import uvicorn  # noqa: PLC0415

    reload = os.getenv("DEBUG", "").lower() == "true"
    uvicorn.run(
        "pr_test_oracle.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8000,
        reload=reload,
    )
