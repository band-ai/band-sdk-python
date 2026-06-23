# Contributing to Band Python SDK

Thank you for your interest in contributing to the Band Python SDK! This document provides guidelines and instructions for contributing.

## Development Setup

### Prerequisites

- Python 3.11 or higher
- [uv](https://docs.astral.sh/uv/) package manager

### Getting Started

1. **Fork and clone the repository**

   ```bash
   git clone https://github.com/<your-username>/band-sdk-python.git
   cd band-sdk-python
   ```

2. **Add the upstream remote**

   ```bash
   git remote add upstream https://github.com/band-ai/band-sdk-python.git
   ```

3. **Install dependencies**

   ```bash
   uv sync --extra dev
   ```

4. **Activate the virtual environment**

   ```bash
   # macOS/Linux
   source .venv/bin/activate

   # Windows
   .venv\Scripts\activate
   ```

5. **Install pre-commit hooks**

   ```bash
   uv run pre-commit install
   ```

6. **Set up environment variables**

   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

## Development Workflow

1. **Create a feature branch**

   ```bash
   git checkout -b feat/your-feature-name
   # or
   git checkout -b fix/your-bug-fix
   ```

   Use branch prefixes: `feat/`, `fix/`, `refactor/`

2. **Make your changes**

   Follow the code style guidelines below.

3. **Run tests**

   ```bash
   # Run unit tests
   uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v

   # Run with coverage
   uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e --cov=src/band

   # Run a specific test
   uv run pytest tests/ -k "test_name"
   ```

4. **Run code quality checks**

   ```bash
   # Linting
   uv run ruff check .

   # Formatting
   uv run ruff format .

   # Type checking
   uv run pyrefly check
   ```

5. **Run pre-commit checks**

   ```bash
   uv run pre-commit run --all-files
   ```

6. **Commit your changes**

   Write clear commit messages following [Conventional Commits](https://www.conventionalcommits.org/):

   ```bash
   git commit -m "feat: add new platform tool for X"
   git commit -m "fix: resolve WebSocket reconnection issue"
   ```

7. **Submit a pull request**

   Push your branch and open a PR against the `dev` branch.

## Code Style Guidelines

### General Rules

- Always use type hints for function parameters and return types
- Use `from __future__ import annotations` for forward references
- Use absolute imports from `band`
- Use Pydantic for data models and validation
- Follow existing patterns in the codebase

### Logging

Never use `print()` statements. Always use logging:

```python
import logging

logger = logging.getLogger(__name__)

logger.info("Processing request")
logger.error("Failed to connect", exc_info=True)
```

### Docstrings

Use Google-style docstrings for public functions and classes:

```python
from band import Agent


def connect_agent(agent_id: str, api_key: str) -> Agent:
    """Connect an agent to the Band platform.

    Args:
        agent_id: The unique identifier for the agent.
        api_key: The API key for authentication.

    Returns:
        An Agent instance connected to the platform.

    Raises:
        AuthenticationError: If the API key is invalid.
    """
```

### Type Annotations

Use modern Python type annotation syntax:

```python
# Use pipe unions (Python 3.10+)
def process(value: str | None) -> list[str]:
    ...

# Use subscripted generics
def get_items() -> dict[str, list[int]]:
    ...
```

## Running Integration Tests

Integration tests require a valid Band API key:

```bash
# Set up your API key in .env or agent_config.yaml
uv run pytest tests/integration/ -v -s --no-cov
```

## Naming Conventions

### Issue Titles

Use component prefixes to categorize issues:

```
[Component] Brief description
```

**Components:**
- `[SDK]` - Core SDK functionality
- `[Agent]` - Agent connection and lifecycle
- `[API]` - REST API client
- `[WebSocket]` - Real-time WebSocket connections
- `[Integrations]` - Framework integrations (LangGraph, PydanticAI, etc.)
- `[Config]` - Configuration and settings
- `[Docs]` - Documentation
- `[CI]` - CI/CD and workflows
- `[Performance]` - Performance improvements

**Examples:**
- `[Agent] Add automatic reconnection support`
- `[API] Fix authentication token refresh`
- `[Integrations] Add CrewAI support`

### PR Titles

Follow Conventional Commits format:

```
type(scope): description
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`

**Examples:**
- `feat(agent): add event streaming support`
- `fix(api): resolve timeout handling`
- `docs: update integration examples`

PR titles are validated by CI - PRs with invalid titles will fail the check.

## Pull Request Guidelines

1. Ensure all CI checks pass
2. Update documentation if needed
3. Add tests for new functionality
4. Keep PRs focused and atomic
5. Reference related issues in the PR description
6. Use proper PR title format (validated by CI)

## Release Process

This project uses [Release Please](https://github.com/googleapis/release-please) for automated releases. Releases follow [Semantic Versioning](https://semver.org/):

- **MAJOR**: Breaking changes
- **MINOR**: New backward-compatible features
- **PATCH**: Bug fixes

## Getting Help

- Open an issue for bugs or feature requests
- Check existing issues before creating new ones
- Join discussions in pull requests

Thank you for contributing!
