"""Shared fixtures for pr-test-oracle tests."""

import os
from collections.abc import Generator
from unittest.mock import patch

import pytest

from pr_test_oracle.config import Settings
from pr_test_oracle.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    PRInfo,
    TestMapping,
    TestRecommendation,
)


@pytest.fixture
def mock_env_vars() -> Generator[dict[str, str], None, None]:
    """Provide minimal environment variables for Settings."""
    env = {
        "GITHUB_TOKEN": "test-token",
    }
    with patch.dict(os.environ, env, clear=False):
        yield env


@pytest.fixture
def full_env_vars() -> Generator[dict[str, str], None, None]:
    """Provide full environment variables including AI config."""
    env = {
        "AI_PROVIDER": "claude",
        "AI_MODEL": "test-model",
        "GITHUB_TOKEN": "test-token",
    }
    with patch.dict(os.environ, env, clear=False):
        yield env


@pytest.fixture
def settings(mock_env_vars: dict[str, str]) -> Settings:
    """Create Settings instance with mocked environment."""
    return Settings()


@pytest.fixture
def sample_pr_url() -> str:
    """Return a valid sample PR URL."""
    return "https://github.com/owner/repo/pull/123"


@pytest.fixture
def sample_analyze_request(sample_pr_url: str) -> AnalyzeRequest:
    """Create a sample analyze request for testing."""
    return AnalyzeRequest(
        pr_url=sample_pr_url,
        ai_provider="claude",
        ai_model="test-model",
    )


@pytest.fixture
def sample_pr_info(sample_pr_url: str) -> PRInfo:
    """Create a sample PRInfo for testing."""
    return PRInfo(
        owner="owner",
        repo="repo",
        pr_number=123,
        url=sample_pr_url,
    )


@pytest.fixture
def sample_test_recommendation() -> TestRecommendation:
    """Create a sample test recommendation."""
    return TestRecommendation(
        test_file="tests/test_auth.py",
        test_name="TestAuth::test_login",
        reason="Changed auth middleware",
        priority="critical",
        confidence="high",
    )


@pytest.fixture
def sample_analyze_response(
    sample_pr_url: str,
    sample_test_recommendation: TestRecommendation,
) -> AnalyzeResponse:
    """Create a sample analysis response."""
    return AnalyzeResponse(
        pr_url=sample_pr_url,
        ai_provider="claude",
        ai_model="test-model",
        recommendations=[sample_test_recommendation],
        summary="1 test files recommended (1 critical, 0 standard)",
        review_posted=True,
        review_url="https://github.com/owner/repo/pull/123#issuecomment-1",
    )


@pytest.fixture
def sample_test_mapping() -> TestMapping:
    """Create a sample test mapping."""
    return TestMapping(
        source_file="src/pr_test_oracle/auth.py",
        candidate_tests=["tests/test_auth.py"],
        mapping_reason="Naming convention and directory structure mapping",
    )
