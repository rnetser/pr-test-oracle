import asyncio
import os
import subprocess
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simple_logger.logger import get_logger

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))


def _get_ai_cli_timeout() -> int:
    """Parse AI_CLI_TIMEOUT with fallback for invalid values."""
    raw = os.getenv("AI_CLI_TIMEOUT", "10")
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid AI_CLI_TIMEOUT=%s; defaulting to 10", raw)
        return 10
    if value <= 0:
        logger.warning("Non-positive AI_CLI_TIMEOUT=%s; defaulting to 10", raw)
        return 10
    return value


AI_CLI_TIMEOUT = _get_ai_cli_timeout()  # minutes

MAX_CONCURRENT_AI_CALLS = 10


@dataclass(frozen=True)
class ProviderConfig:
    """Configuration for an AI CLI provider."""

    binary: str
    build_cmd: Callable[[str, str, Path | None], list[str]]
    uses_own_cwd: bool = False


def _build_claude_cmd(binary: str, model: str, _cwd: Path | None) -> list[str]:
    return [binary, "--model", model, "--dangerously-skip-permissions", "-p"]


def _build_gemini_cmd(binary: str, model: str, _cwd: Path | None) -> list[str]:
    return [binary, "--model", model, "--yolo"]


def _build_cursor_cmd(binary: str, model: str, cwd: Path | None) -> list[str]:
    cmd = [binary, "--force", "--model", model, "--print"]
    if cwd:
        cmd.extend(["--workspace", str(cwd)])
    return cmd


PROVIDER_CONFIG: dict[str, ProviderConfig] = {
    "claude": ProviderConfig(binary="claude", build_cmd=_build_claude_cmd),
    "gemini": ProviderConfig(binary="gemini", build_cmd=_build_gemini_cmd),
    "cursor": ProviderConfig(
        binary="agent", uses_own_cwd=True, build_cmd=_build_cursor_cmd
    ),
}

VALID_AI_PROVIDERS = set(PROVIDER_CONFIG.keys())


async def run_parallel_with_limit(
    coroutines: list[Any],
    max_concurrency: int = MAX_CONCURRENT_AI_CALLS,
) -> list[Any]:
    """Run coroutines in parallel with bounded concurrency.

    Args:
        coroutines: List of coroutines to execute.
        max_concurrency: Maximum concurrent executions.

    Returns:
        List of results (including exceptions if any failed).
    """
    semaphore = asyncio.Semaphore(max_concurrency)

    async def bounded(coro: Coroutine[Any, Any, Any]) -> object:
        async with semaphore:
            return await coro

    return await asyncio.gather(
        *[bounded(c) for c in coroutines],
        return_exceptions=True,
    )


async def call_ai_cli(
    prompt: str,
    cwd: Path | None = None,
    ai_provider: str = "",
    ai_model: str = "",
    ai_cli_timeout: int | None = None,
) -> tuple[bool, str]:
    """Call AI CLI (Claude, Gemini, or Cursor) with given prompt.

    Args:
        prompt: The prompt to send to the AI CLI.
        cwd: Working directory for AI to explore (typically repo path).
        ai_provider: AI provider to use.
        ai_model: AI model to use.
        ai_cli_timeout: Timeout in minutes (overrides AI_CLI_TIMEOUT env var).

    Returns:
        Tuple of (success, output). success is True with AI output, False with error message.
    """
    config = PROVIDER_CONFIG.get(ai_provider)
    if not config:
        return (
            False,
            f"Unknown AI provider: '{ai_provider}'. "
            f"Valid providers: {', '.join(sorted(VALID_AI_PROVIDERS))}",
        )

    if not ai_model:
        return (
            False,
            "No AI model configured. Set AI_MODEL env var or pass ai_model in request body.",
        )

    provider_info = f"{ai_provider.upper()} ({ai_model})"
    cmd = config.build_cmd(config.binary, ai_model, cwd)

    subprocess_cwd = None if config.uses_own_cwd else cwd

    effective_timeout = ai_cli_timeout or AI_CLI_TIMEOUT
    timeout = effective_timeout * 60  # Convert minutes to seconds

    logger.info("Calling %s CLI", provider_info)

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=subprocess_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=prompt,
        )
    except subprocess.TimeoutExpired:
        return (
            False,
            f"{provider_info} CLI error: Analysis timed out after {effective_timeout} minutes",
        )

    if result.returncode != 0:
        error_detail = result.stderr or result.stdout or "unknown error (no output)"
        return False, f"{provider_info} CLI error: {error_detail}"

    logger.debug("%s CLI response length: %d chars", provider_info, len(result.stdout))
    return True, result.stdout
