"""Tests for Pydantic models."""

import pytest

from pr_test_oracle.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    PRInfo,
    TestMapping,
    TestRecommendation,
)


class TestPRInfo:
    """Tests for PRInfo model."""

    def test_create_pr_info(self) -> None:
        info = PRInfo(
            owner="owner",
            repo="repo",
            pr_number=42,
            url="https://github.com/owner/repo/pull/42",
        )
        assert info.owner == "owner"
        assert info.repo == "repo"
        assert info.pr_number == 42


class TestAnalyzeRequest:
    """Tests for AnalyzeRequest model."""

    def test_valid_pr_url(self, sample_pr_url: str) -> None:
        req = AnalyzeRequest(pr_url=sample_pr_url)
        assert req.pr_url == sample_pr_url

    def test_invalid_pr_url_not_github(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub PR URL"):
            AnalyzeRequest(pr_url="https://gitlab.com/owner/repo/pull/123")

    def test_invalid_pr_url_not_pull(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub PR URL"):
            AnalyzeRequest(pr_url="https://github.com/owner/repo/issues/123")

    def test_invalid_pr_url_no_number(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub PR URL"):
            AnalyzeRequest(pr_url="https://github.com/owner/repo/pull/abc")

    def test_parse_pr_info(self, sample_pr_url: str) -> None:
        req = AnalyzeRequest(pr_url=sample_pr_url)
        info = req.parse_pr_info()
        assert isinstance(info, PRInfo)
        assert info.owner == "owner"
        assert info.repo == "repo"
        assert info.pr_number == 123

    def test_optional_fields_default_to_none(self, sample_pr_url: str) -> None:
        req = AnalyzeRequest(pr_url=sample_pr_url)
        assert req.repo_path is None
        assert req.ai_provider is None
        assert req.ai_model is None
        assert req.github_token is None
        assert req.test_patterns is None

    def test_all_fields_populated(self, sample_pr_url: str) -> None:
        req = AnalyzeRequest(
            pr_url=sample_pr_url,
            repo_path="/tmp/repo",
            ai_provider="claude",
            ai_model="sonnet",
            github_token="ghp_test",
            test_patterns=["tests/**/*.py"],
        )
        assert req.repo_path == "/tmp/repo"
        assert req.ai_provider == "claude"

    def test_pr_url_with_dots_and_hyphens(self) -> None:
        """PR URLs with dots and hyphens in owner/repo should be valid."""
        req = AnalyzeRequest(pr_url="https://github.com/my-org/my.repo/pull/1")
        info = req.parse_pr_info()
        assert info.owner == "my-org"
        assert info.repo == "my.repo"


class TestTestRecommendation:
    """Tests for TestRecommendation model."""

    def test_create_recommendation(self) -> None:
        rec = TestRecommendation(
            test_file="tests/test_foo.py",
            reason="Changed foo module",
            priority="critical",
            confidence="high",
        )
        assert rec.test_file == "tests/test_foo.py"
        assert rec.test_name == "(all)"
        assert rec.priority == "critical"

    def test_invalid_priority(self) -> None:
        with pytest.raises(ValueError):
            TestRecommendation(
                test_file="tests/test_foo.py",
                reason="reason",
                priority="urgent",
                confidence="high",
            )

    def test_invalid_confidence(self) -> None:
        with pytest.raises(ValueError):
            TestRecommendation(
                test_file="tests/test_foo.py",
                reason="reason",
                priority="critical",
                confidence="very_high",
            )


class TestAnalyzeResponse:
    """Tests for AnalyzeResponse model."""

    def test_defaults(self) -> None:
        resp = AnalyzeResponse(pr_url="https://github.com/o/r/pull/1")
        assert resp.recommendations == []
        assert resp.summary == ""
        assert resp.review_posted is False
        assert resp.review_url is None

    def test_with_recommendations(
        self, sample_test_recommendation: TestRecommendation
    ) -> None:
        resp = AnalyzeResponse(
            pr_url="https://github.com/o/r/pull/1",
            recommendations=[sample_test_recommendation],
            summary="1 test",
        )
        assert len(resp.recommendations) == 1


class TestTestMapping:
    """Tests for TestMapping model."""

    def test_defaults(self) -> None:
        mapping = TestMapping(source_file="src/foo.py")
        assert mapping.candidate_tests == []
        assert mapping.mapping_reason == ""

    def test_with_candidates(self) -> None:
        mapping = TestMapping(
            source_file="src/foo.py",
            candidate_tests=["tests/test_foo.py", "tests/test_bar.py"],
            mapping_reason="Naming convention",
        )
        assert len(mapping.candidate_tests) == 2
