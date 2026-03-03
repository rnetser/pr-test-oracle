"""Core orchestration: fetch PR → map tests → call AI → format result."""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from simple_logger.logger import get_logger

from pr_test_oracle.ai_client import VALID_AI_PROVIDERS, call_ai_cli
from pr_test_oracle.config import Settings
from pr_test_oracle.github_client import GitHubClient
from pr_test_oracle.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    PRInfo,
    TestMapping,
    TestRecommendation,
)
from pr_test_oracle.test_mapper import TestMapper

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))


def _resolve_ai_config(body: AnalyzeRequest, settings: Settings) -> tuple[str, str]:
    """Resolve AI provider and model from request or settings.

    Request values take precedence over settings/env vars.

    Returns:
        Tuple of (ai_provider, ai_model).

    Raises:
        ValueError: If provider or model is not configured.
    """
    provider = body.ai_provider or settings.ai_provider or ""
    model = body.ai_model or settings.ai_model or ""
    if not provider:
        msg = (
            "No AI provider configured. Set AI_PROVIDER env var or pass "
            f"ai_provider in request body. Valid providers: {', '.join(sorted(VALID_AI_PROVIDERS))}"
        )
        raise ValueError(msg)
    if not model:
        msg = "No AI model configured. Set AI_MODEL env var or pass ai_model in request body."
        raise ValueError(msg)
    return provider, model


def _merge_settings(body: AnalyzeRequest, settings: Settings) -> Settings:
    """Create a copy of settings with per-request overrides applied.

    Request values take precedence over environment variable defaults.
    Only non-None request values are applied as overrides.
    """
    overrides: dict[str, Any] = {}

    direct_fields = [
        "ai_provider",
        "ai_model",
        "ai_cli_timeout",
        "test_patterns",
        "post_comment",
    ]
    for field in direct_fields:
        value = getattr(body, field, None)
        if value is not None:
            overrides[field] = value

    # SecretStr field needs wrapping
    if body.github_token is not None:
        overrides["github_token"] = SecretStr(body.github_token)

    if overrides:
        merged_data = settings.model_dump(mode="python") | overrides
        # model_dump(mode="python") keeps SecretStr objects as-is,
        # but model_validate would double-wrap them. Extract raw values first.
        if (
            "github_token" not in overrides
            and merged_data.get("github_token") is not None
        ):
            token = merged_data["github_token"]
            if isinstance(token, SecretStr):
                merged_data["github_token"] = token.get_secret_value()
        return Settings.model_validate(merged_data)
    return settings


def _build_ai_prompt(
    pr_diff: str,
    test_mappings: list[TestMapping],
    test_contents: dict[str, str],
    custom_prompt: str = "",
) -> str:
    """Build the AI prompt for test recommendation analysis.

    Includes PR diff, pre-computed test mappings, and test file contents.
    """
    parts: list[str] = []

    parts.append(
        "As an expert software testing engineer, analyze all modified files "
        "in this PR and create a targeted test execution plan.\n\n"
        "IMPORTANT: The repository may be a source code project with separate tests, "
        "OR it may be a test suite repository where the changed files ARE the tests. "
        "Adapt your analysis accordingly:\n"
        "- If changed files are source code: recommend which tests verify the changes.\n"
        "- If changed files are themselves tests: recommend running those changed tests, "
        "plus any other tests that share fixtures, utilities, or base classes with them.\n"
        "- If changed files are test utilities/fixtures/conftest: recommend running all "
        "tests that depend on or import from the changed utilities.\n"
    )

    parts.append("## PR Diff\n")
    parts.append(
        "IMPORTANT: Read the actual code changes below carefully. Do NOT "
        "base your recommendations on file names alone. Understand WHAT "
        "changed semantically — new functions, modified logic, changed "
        "signatures, altered control flow, updated configurations.\n"
    )
    parts.append(pr_diff)
    parts.append("\n")

    parts.append("## Pre-computed Test Mappings\n")
    parts.append(
        "Static analysis has identified potential test file matches based on "
        "naming conventions and directory structure. These are CANDIDATES only — "
        "you must verify each one by reading the test file contents below and "
        "confirming it actually tests the changed code paths.\n"
    )
    for mapping in test_mappings:
        parts.append(f"\n### {mapping.source_file}")
        parts.append(f"Mapping reason: {mapping.mapping_reason}")
        if mapping.candidate_tests:
            parts.extend(f"  - {test}" for test in mapping.candidate_tests)
        else:
            parts.append("  (no direct mapping found)")
    parts.append("\n")

    if test_contents:
        parts.append("## Test File Contents\n")
        parts.append(
            "CRITICAL: Read each test file below thoroughly. Understand what "
            "each test function/class actually verifies. Only recommend a test "
            "if you can explain the specific connection between the PR changes "
            "and what the test validates.\n"
        )
        for file_path, content in test_contents.items():
            # Detect language from file extension for proper syntax highlighting
            lang = _detect_language(file_path)
            parts.append(f"\n### {file_path}\n```{lang}\n{content}\n```\n")

    parts.append("## Analysis Instructions\n")
    parts.append(
        """For each changed file in the PR, determine:
1. If the changed file IS a test: recommend running it (the test itself was modified and needs execution)
2. If the changed file is a test utility, fixture, or conftest: recommend running ALL tests that import or depend on it
3. If the changed file is source code: recommend which tests DIRECTLY verify the changed code paths
4. Which other tests could BREAK due to downstream dependencies (imports, shared state, API contracts)
5. Whether any changes are purely cosmetic (formatting, comments, whitespace) and need NO testing

Be SELECTIVE — only recommend tests with a clear, explainable connection to the changes.
Do NOT recommend tests just because they are in the same module or have a similar name.
A test must actually exercise code paths affected by this PR.

For each recommended test, provide:
- test_file: path to the test file
- test_name: specific test class/function if applicable, or "(all)" if the entire file should run
- reason: a SPECIFIC explanation of how the PR changes affect what
  this test verifies (not generic statements like "tests the module")
- priority: "critical" (directly verifies changed code) or
  "standard" (regression safety for dependent code)
- confidence: "high" (certain), "medium" (likely), or
  "low" (possibly affected)

Your response must be ONLY a valid JSON array. No text before or after. No markdown code blocks.

Example:
[
  {
    "test_file": "tests/test_auth.py",
    "test_name": "TestAuth::test_login_flow",
    "reason": "PR modifies hash_password() in auth.py; this test calls login() which invokes it",
    "priority": "critical",
    "confidence": "high"
  }
]
"""
    )

    # Append custom prompt instructions if provided
    if custom_prompt:
        parts.append("## Additional Instructions\n")
        parts.append(custom_prompt)
        parts.append("\n")

    return "\n".join(parts)


def _detect_language(file_path: str) -> str:
    """Detect programming language from file extension for syntax highlighting."""
    ext = file_path.rsplit(".", 1)[-1] if "." in file_path else ""
    language_map = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "jsx": "javascript",
        "tsx": "typescript",
        "go": "go",
        "java": "java",
        "rb": "ruby",
        "rs": "rust",
        "cs": "csharp",
        "php": "php",
        "sh": "bash",
        "bash": "bash",
    }
    return language_map.get(ext, "")


def _parse_items(data: list[dict[str, Any]]) -> list[TestRecommendation]:
    """Parse list of dicts into TestRecommendation, skipping malformed items."""
    results: list[TestRecommendation] = []
    for item in data:
        try:
            results.append(TestRecommendation(**item))
        except (TypeError, KeyError, ValueError):
            logger.warning("Skipping malformed recommendation item: %s", item)
    return results


def _parse_ai_response(raw_text: str) -> list[TestRecommendation]:
    """Parse AI CLI JSON response into TestRecommendation list.

    Handles common AI response quirks: markdown code blocks, surrounding text.
    """
    text = raw_text.strip()

    # Try parsing directly
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return _parse_items(data)
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
        pass

    # Try extracting from markdown code block
    blocks = re.findall(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    for block in blocks:
        stripped_block = block.strip()
        try:
            data = json.loads(stripped_block)
            if isinstance(data, list):
                return _parse_items(data)
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            continue

    # Try finding JSON array by bracket matching
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, list):
                return _parse_items(data)
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            pass

    logger.warning(
        "Failed to parse AI response as JSON array, returning empty recommendations"
    )
    return []


def _format_pr_comment(
    recommendations: list[TestRecommendation],
    ai_provider: str,
    ai_model: str,
) -> str:
    """Format test recommendations as a PR comment in markdown."""
    parts: list[str] = []

    parts.append("## Tests to Run\n")
    parts.append("The following tests should be run to verify this PR:\n")

    critical = [r for r in recommendations if r.priority == "critical"]
    standard = [r for r in recommendations if r.priority == "standard"]

    for section_title, section_recs in [
        ("### Critical (directly affected)", critical),
        ("### Standard (regression safety)", standard),
    ]:
        if not section_recs:
            continue
        parts.append(section_title)
        for rec in section_recs:
            test_ref = f"`{rec.test_file}"
            if rec.test_name and rec.test_name != "(all)":
                test_ref += f"::{rec.test_name}"
            test_ref += "`"
            confidence = rec.confidence.capitalize()
            line = f"- [ ] {test_ref} — {rec.reason} ({confidence} confidence)"
            parts.append(line)
        parts.append("")

    if not recommendations:
        parts.append(
            "No tests need to run for this PR. The changes do not appear "
            "to affect any testable code paths.\n"
        )

    # Summary
    parts.append("### Summary")
    unique_files = len({r.test_file for r in recommendations})
    parts.append(
        f"- **{unique_files} test files** recommended ({len(critical)} critical, {len(standard)} standard)"
    )
    parts.append(f"- AI Provider: {ai_provider.capitalize()} ({ai_model})")

    return "\n".join(parts)


async def _clone_pr_repo(
    gh_client: GitHubClient,
    pr_info: PRInfo,
    repo_path: str,
) -> None:
    """Clone the PR head branch, handling fork PRs.

    For fork PRs, clones from the fork owner's repo instead of the base repo.
    """
    pr_details = await gh_client.get_pr_details(pr_info)
    head_branch = pr_details.get("headRefName", "")
    # For fork PRs, use fork owner and repo name
    head_owner_info = pr_details.get("headRepositoryOwner", {})
    head_repo_info = pr_details.get("headRepository", {})
    if isinstance(head_owner_info, dict):
        clone_owner = head_owner_info.get("login", pr_info.owner)
    else:
        clone_owner = pr_info.owner
    if isinstance(head_repo_info, dict):
        clone_repo = head_repo_info.get("name", pr_info.repo)
    else:
        clone_repo = pr_info.repo
    await gh_client.clone_repo(
        clone_owner,
        clone_repo,
        repo_path,
        branch=head_branch,
    )


async def analyze_pr(
    body: AnalyzeRequest,
    settings: Settings,
) -> AnalyzeResponse:
    """Analyze a PR and return test recommendations.

    This is the main orchestration function:
    1. Parse PR URL to extract owner/repo/number
    2. Fetch PR diff and changed files from GitHub
    3. Map changed files to candidate test files (static analysis)
    4. Send PR diff + test mapping + test contents to AI
    5. Parse AI response into structured recommendations
    6. Post review on PR
    7. Return response

    Args:
        body: The analyze request.
        settings: Application settings (already merged with request overrides).

    Returns:
        AnalyzeResponse with recommendations and metadata.
    """
    # Resolve AI config
    ai_provider, ai_model = _resolve_ai_config(body, settings)

    # Parse PR info
    pr_info = body.parse_pr_info()
    logger.info(
        "Analyzing PR #%d in %s/%s", pr_info.pr_number, pr_info.owner, pr_info.repo
    )

    # Create GitHub client
    github_token = body.github_token
    if not github_token and settings.github_token:
        github_token = settings.github_token.get_secret_value()
    if not github_token:
        msg = "No GitHub token configured. Set GITHUB_TOKEN env var or pass github_token in request body."
        raise ValueError(msg)

    gh_client = GitHubClient(token=github_token)

    # Fetch PR data (diff and files in parallel would be nice, but diff contains file info)
    pr_diff = await gh_client.get_pr_diff(pr_info)
    changed_files = await gh_client.get_pr_files(pr_info)

    logger.info("PR has %d changed files", len(changed_files))

    # Validate repo_path if provided by the user
    if body.repo_path:
        repo_resolved = Path(body.repo_path).resolve()
        if not repo_resolved.is_dir():
            msg = f"repo_path does not exist or is not a directory: {body.repo_path}"
            raise ValueError(msg)
        repo_path = str(repo_resolved)
    else:
        repo_path = None

    # Determine repo path for test mapping
    cleanup_repo = False

    try:
        if not repo_path:
            # Clone the repo and checkout the PR head branch
            repo_path = tempfile.mkdtemp(prefix="pr-test-oracle-")
            cleanup_repo = True
            await _clone_pr_repo(gh_client, pr_info, repo_path)

        # Map changed files to test files
        test_patterns = body.test_patterns or settings.test_patterns
        # Validate test_patterns to prevent path traversal
        for pattern in test_patterns:
            if ".." in pattern:
                msg = f"Invalid test pattern (contains '..'): {pattern}"
                raise ValueError(msg)
            if pattern.startswith("/"):
                msg = f"Invalid test pattern (absolute path): {pattern}"
                raise ValueError(msg)
        mapper = TestMapper(repo_path, test_patterns)
        test_mappings = mapper.map_changed_files(changed_files)

        # Collect all candidate test files
        all_candidates = {t for m in test_mappings for t in m.candidate_tests}

        # Read test file contents for AI context
        test_contents = mapper.get_test_file_contents(sorted(all_candidates))

        # Determine custom prompt: request raw_prompt > repo auto-discovery
        custom_prompt = (body.raw_prompt or "").strip()
        if custom_prompt:
            logger.debug("Using raw prompt from the request")

        elif (Path(repo_path) / "TESTS_ORACLE_PROMPT.md").is_file():
            prompt_path = Path(repo_path) / "TESTS_ORACLE_PROMPT.md"
            try:
                custom_prompt = prompt_path.read_text(encoding="utf-8").strip()
                logger.debug("Using prompt file from the repo: %s", prompt_path)
            except (OSError, UnicodeDecodeError):
                logger.warning("Failed to read prompt file: %s", prompt_path)
                custom_prompt = ""

        else:
            logger.debug("No custom prompt was provided.")
            custom_prompt = ""

        # Build AI prompt
        prompt = _build_ai_prompt(pr_diff, test_mappings, test_contents, custom_prompt)

        # Call AI
        ai_cli_timeout = body.ai_cli_timeout or settings.ai_cli_timeout
        success, output = await call_ai_cli(
            prompt=prompt,
            cwd=Path(repo_path),
            ai_provider=ai_provider,
            ai_model=ai_model,
            ai_cli_timeout=ai_cli_timeout,
        )

        if not success:
            logger.error("AI CLI call failed: %s", output)
            return AnalyzeResponse(
                pr_url=body.pr_url,
                ai_provider=ai_provider,
                ai_model=ai_model,
                summary=f"AI analysis failed: {output}",
            )

        # Parse AI response
        recommendations = _parse_ai_response(output)

        critical_count = sum(1 for r in recommendations if r.priority == "critical")
        standard_count = sum(1 for r in recommendations if r.priority == "standard")

        logger.info(
            "AI recommended %d tests (%d critical, %d standard)",
            len(recommendations),
            critical_count,
            standard_count,
        )

        # Build summary
        unique_files = len({r.test_file for r in recommendations})
        summary = (
            f"{unique_files} test files recommended "
            f"({critical_count} critical, {standard_count} standard)"
        )

        # Post PR review
        review_posted = False
        review_url = None

        # Determine if we should post to the PR
        should_post = (
            body.post_comment
            if body.post_comment is not None
            else settings.post_comment
        )

        if not should_post:
            logger.info("Skipping PR comment (post_comment=false)")
        elif recommendations:
            comment_body = _format_pr_comment(recommendations, ai_provider, ai_model)
            try:
                review_url, review_posted = await gh_client.post_review(
                    pr_info, comment_body
                )
                logger.info("Posted PR review: %s", review_url)
            except RuntimeError:
                logger.exception("Failed to post PR review")
        else:
            comment_body = _format_pr_comment(recommendations, ai_provider, ai_model)
            try:
                review_url = await gh_client.post_comment(pr_info, comment_body)
                review_posted = False
                logger.info("Posted PR comment: %s", review_url)
            except RuntimeError:
                logger.exception("Failed to post PR comment")

        return AnalyzeResponse(
            pr_url=body.pr_url,
            ai_provider=ai_provider,
            ai_model=ai_model,
            recommendations=recommendations,
            summary=summary,
            review_posted=review_posted,
            review_url=review_url,
        )

    finally:
        if cleanup_repo and repo_path:
            shutil.rmtree(repo_path, ignore_errors=True)
            logger.debug("Cleaned up temporary repo at %s", repo_path)
