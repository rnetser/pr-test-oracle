"""GitHub operations: fetch PR data and post comments via gh CLI."""

import asyncio
import json
import os
import re
import subprocess
from typing import Any

from simple_logger.logger import get_logger

from pr_test_oracle.models import PRInfo

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))


def _parse_first_diff_line(diff: str) -> tuple[str, int]:
    """Parse a unified diff to find the first changed file and line number.

    Looks for the first added line (+) in the diff and returns the file path
    and line number, which can be used for a review comment.

    Returns:
        Tuple of (file_path, line_number). Returns ("", 0) if parsing fails.
    """
    current_file = ""
    current_line = 0

    for line in diff.splitlines():
        # Track current file from diff headers
        if line.startswith("+++ b/"):
            current_file = line[6:]  # Strip "+++ b/" prefix
            continue

        # Track line numbers from hunk headers: @@ -old,count +new,count @@
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                current_line = int(match.group(1))
            continue

        # Found an added line — this is a valid target
        if line.startswith("+") and not line.startswith("+++"):
            if current_file and current_line > 0:
                return current_file, current_line
            continue

        # Track line numbers for context and removed lines
        if line.startswith("-") and not line.startswith("---"):
            continue  # Removed lines don't increment new-file line counter
        if not line.startswith("\\"):
            current_line += 1  # Context line — increment

    return "", 0


class GitHubClient:
    """Client for GitHub operations using the gh CLI."""

    def __init__(self, token: str | None = None) -> None:
        """Initialize with optional GitHub token.

        If token is provided, it's set as GH_TOKEN env var for gh CLI.
        Otherwise, gh CLI uses its own auth.
        """
        self._env = os.environ.copy()
        if token:
            self._env["GH_TOKEN"] = token

    async def get_pr_diff(self, pr_info: PRInfo) -> str:
        """Fetch the full diff for a PR.

        Uses: gh pr diff {pr_number} --repo {owner}/{repo}

        Returns the diff as a string.
        Raises RuntimeError on failure.
        """
        cmd = [
            "gh",
            "pr",
            "diff",
            str(pr_info.pr_number),
            "--repo",
            f"{pr_info.owner}/{pr_info.repo}",
        ]
        return await self._run_gh(cmd, f"fetch diff for PR #{pr_info.pr_number}")

    async def get_pr_files(self, pr_info: PRInfo) -> list[str]:
        """Get list of changed file paths in a PR.

        Uses: gh pr diff {pr_number} --repo {owner}/{repo} --name-only

        Returns list of file paths.
        """
        cmd = [
            "gh",
            "pr",
            "diff",
            str(pr_info.pr_number),
            "--repo",
            f"{pr_info.owner}/{pr_info.repo}",
            "--name-only",
        ]
        output = await self._run_gh(cmd, f"list files for PR #{pr_info.pr_number}")
        return [f.strip() for f in output.strip().splitlines() if f.strip()]

    async def get_pr_details(self, pr_info: PRInfo) -> dict[str, Any]:
        """Get PR metadata (title, body, base branch, head branch, etc.).

        Uses: gh pr view {pr_number} --repo {owner}/{repo}
        --json title,body,baseRefName,headRefName,url

        Returns parsed JSON dict.
        """
        cmd = [
            "gh",
            "pr",
            "view",
            str(pr_info.pr_number),
            "--repo",
            f"{pr_info.owner}/{pr_info.repo}",
            "--json",
            "title,body,baseRefName,headRefName,headRepositoryOwner,headRepository,headRefOid,url",
        ]
        output = await self._run_gh(cmd, f"view PR #{pr_info.pr_number}")
        return json.loads(output)

    async def post_comment(self, pr_info: PRInfo, body: str) -> str | None:
        """Post a comment on a PR.

        Uses: gh pr comment {pr_number} --repo {owner}/{repo} --body {body}

        Returns the comment URL if available, None otherwise.
        """
        cmd = [
            "gh",
            "pr",
            "comment",
            str(pr_info.pr_number),
            "--repo",
            f"{pr_info.owner}/{pr_info.repo}",
            "--body",
            body,
        ]
        logger.info(
            "Posting comment on PR #%d in %s/%s",
            pr_info.pr_number,
            pr_info.owner,
            pr_info.repo,
        )
        output = await self._run_gh(cmd, f"post comment on PR #{pr_info.pr_number}")
        # gh pr comment prints the comment URL on success
        url = output.strip()
        return url if url.startswith("https://") else None

    async def post_review(self, pr_info: PRInfo, body: str) -> tuple[str | None, bool]:
        """Post a file-level review comment on a PR.

        Uses the pull request comments API to create a file-level comment
        attached to the first changed file. File-level comments appear in
        the Files Changed tab and have a "Resolve conversation" button.

        Falls back to a regular PR comment if the review API fails.

        Args:
            pr_info: PR information.
            body: The review comment content (markdown).

        Returns:
            Tuple of (url, is_review). is_review is True if a file-level
            review comment was posted, False if it fell back to a regular comment.
        """
        # Get changed files and head commit SHA
        changed_files = await self.get_pr_files(pr_info)
        if not changed_files:
            logger.warning("No changed files found, falling back to regular comment")
            url = await self.post_comment(pr_info, body)
            return url, False

        pr_details = await self.get_pr_details(pr_info)
        commit_id = pr_details.get("headRefOid", "")
        if not commit_id:
            logger.warning(
                "Could not get head commit SHA, falling back to regular comment"
            )
            url = await self.post_comment(pr_info, body)
            return url, False

        changed_file = changed_files[0]

        payload = json.dumps(
            {
                "commit_id": commit_id,
                "path": changed_file,
                "body": body,
                "subject_type": "file",
            }
        )

        cmd = [
            "gh",
            "api",
            "--method",
            "POST",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            f"repos/{pr_info.owner}/{pr_info.repo}/pulls/{pr_info.pr_number}/comments",
            "--input",
            "-",
        ]
        logger.info(
            "Posting file-level review comment on PR #%d in %s/%s (file: %s)",
            pr_info.pr_number,
            pr_info.owner,
            pr_info.repo,
            changed_file,
        )

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=self._env,
                input=payload,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"gh CLI timed out: post review on PR #{pr_info.pr_number}"
            raise RuntimeError(msg) from exc

        if result.returncode != 0:
            error_detail = result.stderr or result.stdout or "unknown error"
            logger.warning(
                "Review comment API failed (%s), falling back to regular comment",
                error_detail.strip(),
            )
            url = await self.post_comment(pr_info, body)
            return url, False

        try:
            data = json.loads(result.stdout)
            return data.get("html_url"), True
        except (json.JSONDecodeError, AttributeError):
            return None, True

    async def clone_repo(
        self,
        owner: str,
        repo: str,
        target_path: str,
        *,
        depth: int = 1,
        branch: str = "",
    ) -> None:
        """Shallow clone a repository, optionally checking out a specific branch.

        Uses: gh repo clone {owner}/{repo} {target_path} -- --depth {depth} [--branch {branch}]
        """
        cmd = [
            "gh",
            "repo",
            "clone",
            f"{owner}/{repo}",
            target_path,
            "--",
            f"--depth={depth}",
        ]
        if branch:
            cmd.append(f"--branch={branch}")
        await self._run_gh(cmd, f"clone {owner}/{repo}")
        logger.info("Cloned %s/%s to %s", owner, repo, target_path)

    async def _run_gh(self, cmd: list[str], description: str) -> str:
        """Run a gh CLI command and return stdout.

        Args:
            cmd: Command and arguments.
            description: Human-readable description for logging/errors.

        Returns:
            stdout output from the command.

        Raises:
            RuntimeError: If the command fails.
        """
        logger.debug("Running: %s", " ".join(cmd))
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=self._env,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"gh CLI timed out: {description}"
            raise RuntimeError(msg) from exc

        if result.returncode != 0:
            error_detail = result.stderr or result.stdout or "unknown error"
            msg = f"gh CLI failed to {description}: {error_detail}"
            raise RuntimeError(msg)

        return result.stdout
