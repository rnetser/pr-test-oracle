# Project Coding Principles

## Data Integrity

- Never truncate data arbitrarily (no `[:100]` or `[:2000]` slicing)
- Preserve full information; let consumers handle their own limits

## No Dead Code

- Use everything you create: imports, variables, clones, instantiations
- Remove unused code rather than leaving it dormant

## Parallel Execution

- Run independent, stateless operations in parallel
- Handle failures gracefully: one failure should not crash all parallel tasks
- Capture exceptions and continue processing

## Architecture

### CLI-Based AI Integration

This project uses AI CLI tools (Claude CLI, Gemini CLI, Cursor Agent CLI) instead of direct SDK integrations:

- **No SDK dependencies**: AI providers are called via subprocess
- **Provider-agnostic**: Easy to add new AI CLIs
- **Auth handled externally**: CLIs manage their own authentication
- **Environment-driven**: `AI_PROVIDER` env var selects the provider

### Key Components

| Component | Purpose |
|-----------|---------|
| `call_ai_cli()` | Single function for all AI CLI calls |
| `run_parallel_with_limit()` | Bounded parallel execution |
| `GitHubClient` | Fetches PR diffs, posts comments via gh CLI |
| `TestMapper` | Maps changed files to candidate test files |

### Logging

Uses `python-simple-logger`:
- INFO: Milestones (analysis started, AI calls, completed)
- DEBUG: Detailed operations (response lengths, extracted data)
- Configured via `LOG_LEVEL` environment variable

## API Design

### Environment Variable / Payload Parity

Every environment variable that configures the service must also be available as a per-request field in the API payload. This allows callers to override any configuration on a per-request basis.

When adding a new environment variable:
1. Add the field to `Settings` in `config.py`
2. Add the corresponding request field to `AnalyzeRequest` in `models.py`
3. Add the field to `_merge_settings()` in `analyzer.py`
