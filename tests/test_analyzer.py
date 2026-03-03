"""Tests for analyzer module."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pr_test_oracle.analyzer import (
    _build_ai_prompt,
    _format_pr_comment,
    _merge_settings,
    _parse_ai_response,
    _resolve_ai_config,
    analyze_pr,
)
from pr_test_oracle.config import Settings
from pr_test_oracle.models import (
    AnalyzeRequest,
    TestMapping,
    TestRecommendation,
)


class TestResolveAiConfig:
    """Tests for _resolve_ai_config."""

    def test_from_request(self) -> None:
        body = AnalyzeRequest(
            pr_url="https://github.com/o/r/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
        )
        settings = Settings(github_token="test-token")
        provider, model = _resolve_ai_config(body, settings)
        assert provider == "claude"
        assert model == "sonnet"

    def test_from_settings(self) -> None:
        body = AnalyzeRequest(pr_url="https://github.com/o/r/pull/1")
        settings = Settings(
            github_token="test-token", ai_provider="gemini", ai_model="pro"
        )
        provider, model = _resolve_ai_config(body, settings)
        assert provider == "gemini"
        assert model == "pro"

    def test_missing_provider_raises(self) -> None:
        body = AnalyzeRequest(pr_url="https://github.com/o/r/pull/1")
        settings = Settings(github_token="test-token")
        with pytest.raises(ValueError, match="No AI provider configured"):
            _resolve_ai_config(body, settings)

    def test_missing_model_raises(self) -> None:
        body = AnalyzeRequest(
            pr_url="https://github.com/o/r/pull/1",
            ai_provider="claude",
        )
        settings = Settings(github_token="test-token")
        with pytest.raises(ValueError, match="No AI model configured"):
            _resolve_ai_config(body, settings)

    def test_request_overrides_settings(self) -> None:
        body = AnalyzeRequest(
            pr_url="https://github.com/o/r/pull/1",
            ai_provider="cursor",
            ai_model="gpt-4",
        )
        settings = Settings(
            github_token="test-token", ai_provider="claude", ai_model="sonnet"
        )
        provider, model = _resolve_ai_config(body, settings)
        assert provider == "cursor"
        assert model == "gpt-4"


class TestMergeSettings:
    """Tests for _merge_settings."""

    def test_no_overrides(self) -> None:
        body = AnalyzeRequest(pr_url="https://github.com/o/r/pull/1")
        settings = Settings(github_token="test-token")
        merged = _merge_settings(body, settings)
        assert merged is settings  # Same object when no overrides

    def test_overrides_applied(self) -> None:
        body = AnalyzeRequest(
            pr_url="https://github.com/o/r/pull/1",
            ai_provider="gemini",
        )
        settings = Settings(github_token="test-token", ai_provider="claude")
        merged = _merge_settings(body, settings)
        assert merged is not settings
        assert merged.ai_provider == "gemini"

    def test_github_token_wrapped(self) -> None:
        body = AnalyzeRequest(
            pr_url="https://github.com/o/r/pull/1",
            github_token="ghp_test123",
        )
        settings = Settings(github_token="test-token")
        merged = _merge_settings(body, settings)
        assert merged.github_token is not None
        assert merged.github_token.get_secret_value() == "ghp_test123"


class TestParseAiResponse:
    """Tests for _parse_ai_response."""

    def test_valid_json_array(self) -> None:
        data = [
            {
                "test_file": "tests/test_auth.py",
                "test_name": "(all)",
                "reason": "Changed auth",
                "priority": "critical",
                "confidence": "high",
            }
        ]
        result = _parse_ai_response(json.dumps(data))
        assert len(result) == 1
        assert result[0].test_file == "tests/test_auth.py"
        assert result[0].priority == "critical"

    def test_json_in_code_block(self) -> None:
        json_str = json.dumps(
            [
                {
                    "test_file": "t.py",
                    "reason": "r",
                    "priority": "standard",
                    "confidence": "low",
                }
            ]
        )
        text = f"```json\n{json_str}\n```"
        result = _parse_ai_response(text)
        assert len(result) == 1
        assert result[0].test_file == "t.py"

    def test_json_with_surrounding_text(self) -> None:
        json_str = json.dumps(
            [
                {
                    "test_file": "t.py",
                    "reason": "r",
                    "priority": "critical",
                    "confidence": "medium",
                }
            ]
        )
        text = f"Here are my recommendations:\n{json_str}\nHope this helps!"
        result = _parse_ai_response(text)
        assert len(result) == 1

    def test_invalid_json_returns_empty(self) -> None:
        result = _parse_ai_response("This is not JSON at all")
        assert result == []

    def test_empty_array(self) -> None:
        result = _parse_ai_response("[]")
        assert result == []

    def test_malformed_item_skipped(self) -> None:
        """Malformed items should be skipped, valid ones kept."""
        data = json.dumps(
            [
                {
                    "test_file": "tests/test_auth.py",
                    "reason": "r",
                    "priority": "critical",
                    "confidence": "high",
                },
                {"bad_field": "invalid"},
                {
                    "test_file": "tests/test_api.py",
                    "reason": "r2",
                    "priority": "standard",
                    "confidence": "low",
                },
            ]
        )
        result = _parse_ai_response(data)
        assert len(result) == 2
        assert result[0].test_file == "tests/test_auth.py"
        assert result[1].test_file == "tests/test_api.py"


class TestBuildAiPrompt:
    """Tests for _build_ai_prompt."""

    def test_includes_diff(self) -> None:
        prompt = _build_ai_prompt("diff content", [], {})
        assert "diff content" in prompt

    def test_includes_mappings(self) -> None:
        mappings = [
            TestMapping(
                source_file="src/auth.py",
                candidate_tests=["tests/test_auth.py"],
                mapping_reason="Naming convention",
            )
        ]
        prompt = _build_ai_prompt("diff", mappings, {})
        assert "src/auth.py" in prompt
        assert "tests/test_auth.py" in prompt

    def test_includes_test_contents(self) -> None:
        contents = {"tests/test_auth.py": "def test_login(): pass"}
        prompt = _build_ai_prompt("diff", [], contents)
        assert "def test_login" in prompt

    def test_includes_instructions(self) -> None:
        prompt = _build_ai_prompt("diff", [], {})
        assert "expert software testing engineer" in prompt
        assert "SELECTIVE" in prompt
        assert "JSON array" in prompt
        assert "priority" in prompt

    def test_includes_custom_prompt(self) -> None:
        prompt = _build_ai_prompt(
            "diff", [], {}, "Always prioritize integration tests over unit tests."
        )
        assert "Always prioritize integration tests" in prompt
        assert "Additional Instructions" in prompt

    def test_empty_custom_prompt_ignored(self) -> None:
        prompt = _build_ai_prompt("diff", [], {}, "")
        assert "Additional Instructions" not in prompt
        assert "diff" in prompt


class TestFormatPrComment:
    """Tests for _format_pr_comment."""

    def test_with_recommendations(self) -> None:
        recs = [
            TestRecommendation(
                test_file="tests/test_auth.py",
                test_name="TestAuth::test_login",
                reason="Changed auth",
                priority="critical",
                confidence="high",
            ),
            TestRecommendation(
                test_file="tests/test_api.py",
                reason="API depends on auth",
                priority="standard",
                confidence="medium",
            ),
        ]
        comment = _format_pr_comment(recs, "claude", "sonnet")
        assert "Tests to Run" in comment
        assert "Critical" in comment
        assert "Standard" in comment
        expected_critical = "- [ ] `tests/test_auth.py::TestAuth::test_login` — Changed auth (High confidence)"
        assert expected_critical in comment
        expected_standard = (
            "- [ ] `tests/test_api.py` — API depends on auth (Medium confidence)"
        )
        assert expected_standard in comment
        assert "2 test files" in comment

    def test_empty_recommendations(self) -> None:
        comment = _format_pr_comment([], "claude", "sonnet")
        assert "No tests need to run for this PR" in comment

    def test_only_critical(self) -> None:
        recs = [
            TestRecommendation(
                test_file="tests/test_auth.py",
                reason="reason",
                priority="critical",
                confidence="high",
            ),
        ]
        comment = _format_pr_comment(recs, "claude", "sonnet")
        assert "Critical" in comment
        assert "- [ ] `tests/test_auth.py` — reason (High confidence)" in comment
        assert "Standard" not in comment or "0 standard" in comment


class TestAnalyzePr:
    """Tests for the main analyze_pr function."""

    async def test_successful_analysis(self, tmp_path: Path) -> None:
        body = AnalyzeRequest(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
            repo_path=str(tmp_path),
        )
        settings = Settings(github_token="test-token")

        ai_response = json.dumps(
            [
                {
                    "test_file": "tests/test_auth.py",
                    "test_name": "(all)",
                    "reason": "Changed auth",
                    "priority": "critical",
                    "confidence": "high",
                }
            ]
        )

        with (
            patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
            patch("pr_test_oracle.analyzer.TestMapper") as mock_mapper_class,
            patch(
                "pr_test_oracle.analyzer.call_ai_cli", return_value=(True, ai_response)
            ),
        ):
            mock_gh = mock_gh_class.return_value
            mock_gh.get_pr_diff = AsyncMock(return_value="diff content")
            mock_gh.get_pr_files = AsyncMock(return_value=["src/auth.py"])
            mock_gh.post_review = AsyncMock(
                return_value=(
                    "https://github.com/owner/repo/pull/1#pullrequestreview-1",
                    True,
                )
            )

            mock_mapper = mock_mapper_class.return_value
            mock_mapper.map_changed_files.return_value = [
                TestMapping(
                    source_file="src/auth.py", candidate_tests=["tests/test_auth.py"]
                )
            ]
            mock_mapper.get_test_file_contents.return_value = {}

            result = await analyze_pr(body, settings)

        assert result.pr_url == "https://github.com/owner/repo/pull/1"
        assert len(result.recommendations) == 1
        assert result.recommendations[0].test_file == "tests/test_auth.py"
        assert result.review_posted is True
        assert (
            result.review_url
            == "https://github.com/owner/repo/pull/1#pullrequestreview-1"
        )

    async def test_ai_failure_returns_error_response(self, tmp_path: Path) -> None:
        body = AnalyzeRequest(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
            repo_path=str(tmp_path),
        )
        settings = Settings(github_token="test-token")

        with (
            patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
            patch("pr_test_oracle.analyzer.TestMapper") as mock_mapper_class,
            patch(
                "pr_test_oracle.analyzer.call_ai_cli",
                return_value=(False, "CLI error: timeout"),
            ),
        ):
            mock_gh = mock_gh_class.return_value
            mock_gh.get_pr_diff = AsyncMock(return_value="diff")
            mock_gh.get_pr_files = AsyncMock(return_value=["src/foo.py"])

            mock_mapper = mock_mapper_class.return_value
            mock_mapper.map_changed_files.return_value = []
            mock_mapper.get_test_file_contents.return_value = {}

            result = await analyze_pr(body, settings)

        assert "failed" in result.summary.lower()
        assert result.recommendations == []

    async def test_invalid_repo_path_raises(self) -> None:
        """Non-existent repo_path should raise ValueError."""
        body = AnalyzeRequest(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
            repo_path="/nonexistent/path/xyz",
        )
        settings = Settings(github_token="test-token")
        with (
            patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
        ):
            mock_gh = mock_gh_class.return_value
            mock_gh.get_pr_diff = AsyncMock(return_value="diff")
            mock_gh.get_pr_files = AsyncMock(return_value=["src/foo.py"])
            with pytest.raises(ValueError, match="does not exist"):
                await analyze_pr(body, settings)

    async def test_invalid_test_pattern_traversal_raises(self) -> None:
        """Test patterns with '..' should raise ValueError."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            body = AnalyzeRequest(
                pr_url="https://github.com/owner/repo/pull/1",
                ai_provider="claude",
                ai_model="sonnet",
                repo_path=tmp,
                test_patterns=["../../etc/passwd"],
            )
            settings = Settings(github_token="test-token")
            with (
                patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
            ):
                mock_gh = mock_gh_class.return_value
                mock_gh.get_pr_diff = AsyncMock(return_value="diff")
                mock_gh.get_pr_files = AsyncMock(return_value=["src/foo.py"])
                with pytest.raises(ValueError, match="Invalid test pattern"):
                    await analyze_pr(body, settings)

    async def test_invalid_test_pattern_absolute_raises(self) -> None:
        """Test patterns with absolute paths should raise ValueError."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            body = AnalyzeRequest(
                pr_url="https://github.com/owner/repo/pull/1",
                ai_provider="claude",
                ai_model="sonnet",
                repo_path=tmp,
                test_patterns=["/etc/passwd"],
            )
            settings = Settings(github_token="test-token")
            with (
                patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
            ):
                mock_gh = mock_gh_class.return_value
                mock_gh.get_pr_diff = AsyncMock(return_value="diff")
                mock_gh.get_pr_files = AsyncMock(return_value=["src/foo.py"])
                with pytest.raises(ValueError, match="absolute path"):
                    await analyze_pr(body, settings)

    async def test_post_comment_false_skips_posting(self, tmp_path: Path) -> None:
        body = AnalyzeRequest(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
            repo_path=str(tmp_path),
            post_comment=False,
        )
        settings = Settings(github_token="test-token")
        ai_response = json.dumps(
            [
                {
                    "test_file": "tests/test_auth.py",
                    "reason": "Changed auth",
                    "priority": "critical",
                    "confidence": "high",
                }
            ]
        )
        with (
            patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
            patch("pr_test_oracle.analyzer.TestMapper") as mock_mapper_class,
            patch(
                "pr_test_oracle.analyzer.call_ai_cli",
                return_value=(True, ai_response),
            ),
        ):
            mock_gh = mock_gh_class.return_value
            mock_gh.get_pr_diff = AsyncMock(return_value="diff")
            mock_gh.get_pr_files = AsyncMock(return_value=["src/auth.py"])
            mock_mapper = mock_mapper_class.return_value
            mock_mapper.map_changed_files.return_value = []
            mock_mapper.get_test_file_contents.return_value = {}

            result = await analyze_pr(body, settings)

        assert len(result.recommendations) == 1
        assert result.review_posted is False
        assert result.review_url is None
        # Verify post_review was NOT called
        mock_gh.post_review.assert_not_called()
        mock_gh.post_comment.assert_not_called()

    async def test_raw_prompt_flows_through(self, tmp_path: Path) -> None:
        """Verify raw_prompt flows from request through to AI prompt."""
        unique_text = "UNIQUE_CUSTOM_INSTRUCTION_12345"

        body = AnalyzeRequest(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
            repo_path=str(tmp_path),
            raw_prompt=unique_text,
            post_comment=False,
        )
        settings = Settings(github_token="test-token")

        ai_response = json.dumps(
            [
                {
                    "test_file": "tests/test_auth.py",
                    "reason": "Changed auth",
                    "priority": "critical",
                    "confidence": "high",
                }
            ]
        )

        captured_prompt = None

        async def mock_call_ai_cli(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return True, ai_response

        with (
            patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
            patch("pr_test_oracle.analyzer.TestMapper") as mock_mapper_class,
            patch("pr_test_oracle.analyzer.call_ai_cli", side_effect=mock_call_ai_cli),
        ):
            mock_gh = mock_gh_class.return_value
            mock_gh.get_pr_diff = AsyncMock(return_value="diff content")
            mock_gh.get_pr_files = AsyncMock(return_value=["src/auth.py"])
            mock_mapper = mock_mapper_class.return_value
            mock_mapper.map_changed_files.return_value = []
            mock_mapper.get_test_file_contents.return_value = {}

            await analyze_pr(body, settings)

        assert captured_prompt is not None
        assert unique_text in captured_prompt
        assert "Additional Instructions" in captured_prompt

    async def test_oracle_prompt_auto_discovered(self, tmp_path: Path) -> None:
        """Verify TESTS_ORACLE_PROMPT.md in repo root is auto-discovered and included in AI prompt."""
        # Create TESTS_ORACLE_PROMPT.md in repo root (tmp_path acts as repo_path)
        oracle_prompt = tmp_path / "TESTS_ORACLE_PROMPT.md"
        unique_text = "UNIQUE_ORACLE_INSTRUCTION_67890"
        oracle_prompt.write_text(unique_text)

        body = AnalyzeRequest(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
            repo_path=str(tmp_path),
            post_comment=False,
        )
        settings = Settings(github_token="test-token")

        ai_response = json.dumps(
            [
                {
                    "test_file": "tests/test_auth.py",
                    "reason": "Changed auth",
                    "priority": "critical",
                    "confidence": "high",
                }
            ]
        )

        captured_prompt = None

        async def mock_call_ai_cli(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return True, ai_response

        with (
            patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
            patch("pr_test_oracle.analyzer.TestMapper") as mock_mapper_class,
            patch("pr_test_oracle.analyzer.call_ai_cli", side_effect=mock_call_ai_cli),
        ):
            mock_gh = mock_gh_class.return_value
            mock_gh.get_pr_diff = AsyncMock(return_value="diff content")
            mock_gh.get_pr_files = AsyncMock(return_value=["src/auth.py"])
            mock_mapper = mock_mapper_class.return_value
            mock_mapper.map_changed_files.return_value = []
            mock_mapper.get_test_file_contents.return_value = {}

            await analyze_pr(body, settings)

        # Verify the custom prompt text made it into the AI prompt
        assert captured_prompt is not None
        assert unique_text in captured_prompt
        assert "Additional Instructions" in captured_prompt

    async def test_oracle_prompt_missing_is_silent(self, tmp_path: Path) -> None:
        """No TESTS_ORACLE_PROMPT.md in repo → no 'Additional Instructions', no error."""
        body = AnalyzeRequest(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
            repo_path=str(tmp_path),
            post_comment=False,
        )
        settings = Settings(github_token="test-token")

        ai_response = json.dumps(
            [
                {
                    "test_file": "tests/test_auth.py",
                    "reason": "Changed auth",
                    "priority": "critical",
                    "confidence": "high",
                }
            ]
        )

        captured_prompt = None

        async def mock_call_ai_cli(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return True, ai_response

        with (
            patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
            patch("pr_test_oracle.analyzer.TestMapper") as mock_mapper_class,
            patch("pr_test_oracle.analyzer.call_ai_cli", side_effect=mock_call_ai_cli),
        ):
            mock_gh = mock_gh_class.return_value
            mock_gh.get_pr_diff = AsyncMock(return_value="diff content")
            mock_gh.get_pr_files = AsyncMock(return_value=["src/auth.py"])
            mock_mapper = mock_mapper_class.return_value
            mock_mapper.map_changed_files.return_value = []
            mock_mapper.get_test_file_contents.return_value = {}

            result = await analyze_pr(body, settings)

        assert captured_prompt is not None
        assert "Additional Instructions" not in captured_prompt
        assert result.recommendations  # Still works fine

    async def test_raw_prompt_used_in_analyze(self, tmp_path: Path) -> None:
        """Raw prompt text from request is included in AI prompt."""
        body = AnalyzeRequest(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
            repo_path=str(tmp_path),
            post_comment=False,
            raw_prompt="USER_SUPPLIED_INSTRUCTION_XYZ",
        )
        settings = Settings(github_token="test-token")

        ai_response = json.dumps(
            [
                {
                    "test_file": "tests/test_auth.py",
                    "reason": "Changed auth",
                    "priority": "critical",
                    "confidence": "high",
                }
            ]
        )

        captured_prompt = None

        async def mock_call_ai_cli(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return True, ai_response

        with (
            patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
            patch("pr_test_oracle.analyzer.TestMapper") as mock_mapper_class,
            patch("pr_test_oracle.analyzer.call_ai_cli", side_effect=mock_call_ai_cli),
        ):
            mock_gh = mock_gh_class.return_value
            mock_gh.get_pr_diff = AsyncMock(return_value="diff content")
            mock_gh.get_pr_files = AsyncMock(return_value=["src/auth.py"])
            mock_mapper = mock_mapper_class.return_value
            mock_mapper.map_changed_files.return_value = []
            mock_mapper.get_test_file_contents.return_value = {}

            await analyze_pr(body, settings)

        assert captured_prompt is not None
        assert "USER_SUPPLIED_INSTRUCTION_XYZ" in captured_prompt
        assert "Additional Instructions" in captured_prompt

    async def test_request_prompt_takes_precedence_over_repo(
        self, tmp_path: Path
    ) -> None:
        """Per-request raw_prompt takes precedence over TESTS_ORACLE_PROMPT.md in repo."""
        # Create repo-level TESTS_ORACLE_PROMPT.md
        oracle_prompt = tmp_path / "TESTS_ORACLE_PROMPT.md"
        oracle_prompt.write_text("REPO_INSTRUCTION_222")

        body = AnalyzeRequest(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
            repo_path=str(tmp_path),
            post_comment=False,
            raw_prompt="REQUEST_INSTRUCTION_111",
        )
        settings = Settings(github_token="test-token")

        ai_response = json.dumps(
            [
                {
                    "test_file": "tests/test_auth.py",
                    "reason": "Changed auth",
                    "priority": "critical",
                    "confidence": "high",
                }
            ]
        )

        captured_prompt = None

        async def mock_call_ai_cli(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return True, ai_response

        with (
            patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
            patch("pr_test_oracle.analyzer.TestMapper") as mock_mapper_class,
            patch("pr_test_oracle.analyzer.call_ai_cli", side_effect=mock_call_ai_cli),
        ):
            mock_gh = mock_gh_class.return_value
            mock_gh.get_pr_diff = AsyncMock(return_value="diff content")
            mock_gh.get_pr_files = AsyncMock(return_value=["src/auth.py"])
            mock_mapper = mock_mapper_class.return_value
            mock_mapper.map_changed_files.return_value = []
            mock_mapper.get_test_file_contents.return_value = {}

            await analyze_pr(body, settings)

        assert captured_prompt is not None
        # Request raw_prompt wins — repo prompt should NOT appear
        assert "REQUEST_INSTRUCTION_111" in captured_prompt
        assert "REPO_INSTRUCTION_222" not in captured_prompt
        assert "Additional Instructions" in captured_prompt

    async def test_whitespace_raw_prompt_falls_back_to_repo(
        self, tmp_path: Path
    ) -> None:
        """Whitespace-only raw_prompt should fall back to repo TESTS_ORACLE_PROMPT.md."""
        oracle_prompt = tmp_path / "TESTS_ORACLE_PROMPT.md"
        oracle_prompt.write_text("REPO_FALLBACK_INSTRUCTION_456")

        body = AnalyzeRequest(
            pr_url="https://github.com/owner/repo/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
            repo_path=str(tmp_path),
            post_comment=False,
            raw_prompt="   ",
        )
        settings = Settings(github_token="test-token")

        ai_response = json.dumps(
            [
                {
                    "test_file": "tests/test_auth.py",
                    "reason": "Changed auth",
                    "priority": "critical",
                    "confidence": "high",
                }
            ]
        )

        captured_prompt = None

        async def mock_call_ai_cli(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return True, ai_response

        with (
            patch("pr_test_oracle.analyzer.GitHubClient") as mock_gh_class,
            patch("pr_test_oracle.analyzer.TestMapper") as mock_mapper_class,
            patch("pr_test_oracle.analyzer.call_ai_cli", side_effect=mock_call_ai_cli),
        ):
            mock_gh = mock_gh_class.return_value
            mock_gh.get_pr_diff = AsyncMock(return_value="diff content")
            mock_gh.get_pr_files = AsyncMock(return_value=["src/auth.py"])
            mock_mapper = mock_mapper_class.return_value
            mock_mapper.map_changed_files.return_value = []
            mock_mapper.get_test_file_contents.return_value = {}

            await analyze_pr(body, settings)

        assert captured_prompt is not None
        assert "REPO_FALLBACK_INSTRUCTION_456" in captured_prompt
        assert "Additional Instructions" in captured_prompt

    async def test_missing_github_token_raises(self) -> None:
        body = AnalyzeRequest(
            pr_url="https://github.com/o/r/pull/1",
            ai_provider="claude",
            ai_model="sonnet",
        )
        env = os.environ.copy()
        env.pop("GITHUB_TOKEN", None)
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()  # No github_token
            assert settings.github_token is None
            with pytest.raises(ValueError, match="No GitHub token configured"):
                await analyze_pr(body, settings)
