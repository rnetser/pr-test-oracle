"""Tests for GitHub client module."""

from unittest.mock import MagicMock, patch

import pytest

from pr_test_oracle.github_client import GitHubClient, _parse_first_diff_line
from pr_test_oracle.models import PRInfo


@pytest.fixture
def gh_client() -> GitHubClient:
    """Create a GitHubClient without token."""
    return GitHubClient()


@pytest.fixture
def gh_client_with_token() -> GitHubClient:
    """Create a GitHubClient with token."""
    return GitHubClient(token="test-token")


@pytest.fixture
def pr_info() -> PRInfo:
    """Create a sample PRInfo."""
    return PRInfo(
        owner="owner",
        repo="repo",
        pr_number=42,
        url="https://github.com/owner/repo/pull/42",
    )


class TestGitHubClientInit:
    """Tests for GitHubClient initialization."""

    def test_init_without_token(self, gh_client: GitHubClient) -> None:
        assert (
            "GH_TOKEN" not in gh_client._env or gh_client._env.get("GH_TOKEN") is None
        )

    def test_init_with_token(self, gh_client_with_token: GitHubClient) -> None:
        assert gh_client_with_token._env["GH_TOKEN"] == "test-token"


class TestGetPrDiff:
    """Tests for get_pr_diff."""

    async def test_returns_diff(self, gh_client: GitHubClient, pr_info: PRInfo) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "diff --git a/file.py b/file.py\n+new line"
        mock_result.stderr = ""

        with patch(
            "pr_test_oracle.github_client.asyncio.to_thread", return_value=mock_result
        ):
            diff = await gh_client.get_pr_diff(pr_info)
        assert "diff --git" in diff

    async def test_failure_raises(
        self, gh_client: GitHubClient, pr_info: PRInfo
    ) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Not found"

        with (
            patch(
                "pr_test_oracle.github_client.asyncio.to_thread",
                return_value=mock_result,
            ),
            pytest.raises(RuntimeError, match="Not found"),
        ):
            await gh_client.get_pr_diff(pr_info)


class TestGetPrFiles:
    """Tests for get_pr_files."""

    async def test_returns_file_list(
        self, gh_client: GitHubClient, pr_info: PRInfo
    ) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "src/auth.py\nsrc/config.py\ntests/test_auth.py\n"
        mock_result.stderr = ""

        with patch(
            "pr_test_oracle.github_client.asyncio.to_thread", return_value=mock_result
        ):
            files = await gh_client.get_pr_files(pr_info)
        assert files == ["src/auth.py", "src/config.py", "tests/test_auth.py"]

    async def test_handles_empty_output(
        self, gh_client: GitHubClient, pr_info: PRInfo
    ) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "\n"
        mock_result.stderr = ""

        with patch(
            "pr_test_oracle.github_client.asyncio.to_thread", return_value=mock_result
        ):
            files = await gh_client.get_pr_files(pr_info)
        assert files == []


class TestPostComment:
    """Tests for post_comment."""

    async def test_returns_url(self, gh_client: GitHubClient, pr_info: PRInfo) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/owner/repo/pull/42#issuecomment-123\n"
        mock_result.stderr = ""

        with patch(
            "pr_test_oracle.github_client.asyncio.to_thread", return_value=mock_result
        ):
            url = await gh_client.post_comment(pr_info, "test comment")
        assert url == "https://github.com/owner/repo/pull/42#issuecomment-123"

    async def test_returns_none_for_non_url(
        self, gh_client: GitHubClient, pr_info: PRInfo
    ) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Comment posted\n"
        mock_result.stderr = ""

        with patch(
            "pr_test_oracle.github_client.asyncio.to_thread", return_value=mock_result
        ):
            url = await gh_client.post_comment(pr_info, "test comment")
        assert url is None


class TestCloneRepo:
    """Tests for clone_repo."""

    async def test_clone_success(self, gh_client: GitHubClient) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch(
            "pr_test_oracle.github_client.asyncio.to_thread", return_value=mock_result
        ):
            await gh_client.clone_repo("owner", "repo", "/tmp/target")

    async def test_clone_failure(self, gh_client: GitHubClient) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        mock_result.stderr = "fatal: repository not found"

        with (
            patch(
                "pr_test_oracle.github_client.asyncio.to_thread",
                return_value=mock_result,
            ),
            pytest.raises(RuntimeError, match="repository not found"),
        ):
            await gh_client.clone_repo("owner", "repo", "/tmp/target")


class TestRunGhTimeout:
    """Tests for _run_gh timeout handling."""

    async def test_timeout_raises(self, gh_client: GitHubClient) -> None:
        import subprocess

        with (
            patch(
                "pr_test_oracle.github_client.asyncio.to_thread",
                side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=120),
            ),
            pytest.raises(RuntimeError, match="timed out"),
        ):
            await gh_client._run_gh(["gh", "test"], "test operation")


class TestPostReview:
    """Tests for post_review."""

    async def test_successful_review(
        self, gh_client: GitHubClient, pr_info: PRInfo
    ) -> None:
        """Test posting a review with a valid diff line."""
        mock_diff_result = MagicMock()
        mock_diff_result.returncode = 0
        mock_diff_result.stdout = "diff --git a/src/auth.py b/src/auth.py\n--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1,3 +1,4 @@\n+import os\n def login():\n     pass\n"
        mock_diff_result.stderr = ""

        mock_review_result = MagicMock()
        mock_review_result.returncode = 0
        mock_review_result.stdout = (
            '{"html_url": "https://github.com/o/r/pull/42#pullrequestreview-1"}'
        )
        mock_review_result.stderr = ""

        with patch(
            "pr_test_oracle.github_client.asyncio.to_thread",
            side_effect=[mock_diff_result, mock_review_result],
        ):
            url, is_review = await gh_client.post_review(pr_info, "test body")
        assert url == "https://github.com/o/r/pull/42#pullrequestreview-1"
        assert is_review is True

    async def test_fallback_to_comment_on_empty_diff(
        self, gh_client: GitHubClient, pr_info: PRInfo
    ) -> None:
        """Test fallback to post_comment when diff has no parseable lines."""
        mock_diff_result = MagicMock()
        mock_diff_result.returncode = 0
        mock_diff_result.stdout = ""  # Empty diff
        mock_diff_result.stderr = ""

        mock_comment_result = MagicMock()
        mock_comment_result.returncode = 0
        mock_comment_result.stdout = "https://github.com/o/r/pull/42#issuecomment-1\n"
        mock_comment_result.stderr = ""

        with patch(
            "pr_test_oracle.github_client.asyncio.to_thread",
            side_effect=[mock_diff_result, mock_comment_result],
        ):
            url, is_review = await gh_client.post_review(pr_info, "test body")
        assert url == "https://github.com/o/r/pull/42#issuecomment-1"
        assert is_review is False

    async def test_review_api_failure(
        self, gh_client: GitHubClient, pr_info: PRInfo
    ) -> None:
        """Test RuntimeError when review API fails."""
        mock_diff_result = MagicMock()
        mock_diff_result.returncode = 0
        mock_diff_result.stdout = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -0,0 +1 @@\n+new\n"
        )
        mock_diff_result.stderr = ""

        mock_review_result = MagicMock()
        mock_review_result.returncode = 1
        mock_review_result.stdout = ""
        mock_review_result.stderr = "422 Unprocessable Entity"

        with patch(
            "pr_test_oracle.github_client.asyncio.to_thread",
            side_effect=[mock_diff_result, mock_review_result],
        ):
            with pytest.raises(RuntimeError, match="422"):
                await gh_client.post_review(pr_info, "test body")


class TestParseFirstDiffLine:
    """Tests for _parse_first_diff_line helper."""

    def test_parses_added_line(self) -> None:
        diff = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1,3 +1,4 @@\n+import os\n def login():\n"
        file_path, line = _parse_first_diff_line(diff)
        assert file_path == "f.py"
        assert line == 1

    def test_empty_diff(self) -> None:
        file_path, line = _parse_first_diff_line("")
        assert file_path == ""
        assert line == 0

    def test_no_added_lines(self) -> None:
        diff = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1,3 +1,2 @@\n-removed line\n context\n"
        file_path, line = _parse_first_diff_line(diff)
        assert file_path == ""
        assert line == 0
