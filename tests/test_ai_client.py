"""Tests for AI client module."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from pr_test_oracle.ai_client import (
    PROVIDER_CONFIG,
    VALID_AI_PROVIDERS,
    call_ai_cli,
    run_parallel_with_limit,
)


class TestProviderConfig:
    """Tests for provider configuration."""

    def test_valid_providers_exist(self) -> None:
        assert "claude" in VALID_AI_PROVIDERS
        assert "gemini" in VALID_AI_PROVIDERS
        assert "cursor" in VALID_AI_PROVIDERS

    def test_claude_cmd(self) -> None:
        config = PROVIDER_CONFIG["claude"]
        cmd = config.build_cmd("claude", "sonnet", None)
        assert cmd[0] == "claude"
        assert "--model" in cmd
        assert "sonnet" in cmd

    def test_gemini_cmd(self) -> None:
        config = PROVIDER_CONFIG["gemini"]
        cmd = config.build_cmd("gemini", "pro", None)
        assert cmd[0] == "gemini"
        assert "--model" in cmd

    def test_cursor_cmd_with_cwd(self) -> None:
        config = PROVIDER_CONFIG["cursor"]
        cmd = config.build_cmd("agent", "gpt-4", Path("/tmp/repo"))
        assert "--workspace" in cmd
        assert "/tmp/repo" in cmd

    def test_cursor_cmd_without_cwd(self) -> None:
        config = PROVIDER_CONFIG["cursor"]
        cmd = config.build_cmd("agent", "gpt-4", None)
        assert "--workspace" not in cmd


class TestCallAiCli:
    """Tests for call_ai_cli function."""

    async def test_unknown_provider(self) -> None:
        success, output = await call_ai_cli(
            prompt="test", ai_provider="unknown", ai_model="model"
        )
        assert success is False
        assert "Unknown AI provider" in output

    async def test_missing_model(self) -> None:
        success, output = await call_ai_cli(
            prompt="test", ai_provider="claude", ai_model=""
        )
        assert success is False
        assert "No AI model configured" in output

    async def test_successful_call(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "AI response text"
        mock_result.stderr = ""

        with patch(
            "pr_test_oracle.ai_client.asyncio.to_thread", return_value=mock_result
        ):
            success, output = await call_ai_cli(
                prompt="analyze this", ai_provider="claude", ai_model="sonnet"
            )
        assert success is True
        assert output == "AI response text"

    async def test_failed_call(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Authentication failed"

        with patch(
            "pr_test_oracle.ai_client.asyncio.to_thread", return_value=mock_result
        ):
            success, output = await call_ai_cli(
                prompt="test", ai_provider="claude", ai_model="sonnet"
            )
        assert success is False
        assert "Authentication failed" in output

    async def test_timeout(self) -> None:
        import subprocess

        with patch(
            "pr_test_oracle.ai_client.asyncio.to_thread",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=600),
        ):
            success, output = await call_ai_cli(
                prompt="test",
                ai_provider="claude",
                ai_model="sonnet",
                ai_cli_timeout=10,
            )
        assert success is False
        assert "timed out" in output


class TestRunParallelWithLimit:
    """Tests for run_parallel_with_limit function."""

    async def test_parallel_execution(self) -> None:
        async def coro(x: int) -> int:
            return x * 2

        results = await run_parallel_with_limit([coro(1), coro(2), coro(3)])
        assert results == [2, 4, 6]

    async def test_exception_handling(self) -> None:
        async def good() -> str:
            return "ok"

        async def bad() -> str:
            msg = "fail"
            raise ValueError(msg)

        results = await run_parallel_with_limit([good(), bad(), good()])
        assert results[0] == "ok"
        assert isinstance(results[1], ValueError)
        assert results[2] == "ok"

    async def test_empty_list(self) -> None:
        results = await run_parallel_with_limit([])
        assert results == []

    async def test_concurrency_limit(self) -> None:
        """Verify that semaphore limits concurrent execution."""
        running = 0
        max_running = 0

        async def track() -> None:
            nonlocal running, max_running
            running += 1
            max_running = max(max_running, running)
            await asyncio.sleep(0.01)
            running -= 1

        await run_parallel_with_limit([track() for _ in range(20)], max_concurrency=5)
        assert max_running <= 5
