"""Tests for FastAPI main application."""

import os
from unittest.mock import AsyncMock, patch

import pytest

from pr_test_oracle.models import AnalyzeResponse, TestRecommendation


@pytest.fixture
def mock_settings():
    """Mock settings for tests."""
    env = {
        "GITHUB_TOKEN": "test-token",
    }
    with patch.dict(os.environ, env, clear=False):
        from pr_test_oracle.config import get_settings

        get_settings.cache_clear()
        yield


@pytest.fixture
def test_client(mock_settings):
    """Create a test client."""
    from starlette.testclient import TestClient

    from pr_test_oracle.main import app

    with TestClient(app) as client:
        yield client


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_check_returns_healthy(self, test_client) -> None:
        response = test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    def test_health_check_method_not_allowed(self, test_client) -> None:
        response = test_client.post("/health")
        assert response.status_code == 405


class TestAnalyzeEndpoint:
    """Tests for the /analyze endpoint."""

    def test_invalid_pr_url(self, test_client) -> None:
        response = test_client.post(
            "/analyze",
            json={"pr_url": "https://not-github.com/foo"},
        )
        assert response.status_code == 422  # Validation error

    def test_missing_pr_url(self, test_client) -> None:
        response = test_client.post("/analyze", json={})
        assert response.status_code == 422

    def test_successful_analysis(self, test_client) -> None:
        mock_response = AnalyzeResponse(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
            recommendations=[
                TestRecommendation(
                    test_file="tests/test_auth.py",
                    reason="Changed auth",
                    priority="critical",
                    confidence="high",
                )
            ],
            summary="1 test files recommended (1 critical, 0 standard)",
            review_posted=False,
        )

        with patch(
            "pr_test_oracle.main.analyze_pr", new_callable=AsyncMock
        ) as mock_analyze:
            mock_analyze.return_value = mock_response
            response = test_client.post(
                "/analyze",
                json={"pr_url": "https://github.com/owner/repo/pull/1"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["pr_url"] == "https://github.com/owner/repo/pull/1"
        assert len(data["recommendations"]) == 1
        assert data["recommendations"][0]["priority"] == "critical"

    def test_ai_config_error(self, test_client) -> None:
        """ValueError from missing AI config should return 400."""
        with patch(
            "pr_test_oracle.main.analyze_pr",
            new_callable=AsyncMock,
            side_effect=ValueError("No AI provider configured"),
        ):
            response = test_client.post(
                "/analyze",
                json={"pr_url": "https://github.com/owner/repo/pull/1"},
            )

        assert response.status_code == 400
        assert "No AI provider configured" in response.json()["detail"]

    def test_with_all_optional_fields(self, test_client) -> None:
        mock_response = AnalyzeResponse(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="gemini",
            ai_model="pro",
            summary="0 test files recommended",
        )

        with patch(
            "pr_test_oracle.main.analyze_pr", new_callable=AsyncMock
        ) as mock_analyze:
            mock_analyze.return_value = mock_response
            response = test_client.post(
                "/analyze",
                json={
                    "pr_url": "https://github.com/owner/repo/pull/1",
                    "ai_provider": "gemini",
                    "ai_model": "pro",
                    "test_patterns": ["tests/**/*.py"],
                    "github_token": "ghp_test",
                },
            )

        assert response.status_code == 200
