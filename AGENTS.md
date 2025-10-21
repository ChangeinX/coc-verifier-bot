# Repository Guidelines

## Project Structure & Module Organization
- `bot.py`, `newsbot.py`, `giveawaybot.py`: Discord bot entry points.
- `tests/`: Pytest suite (`test_*.py`, async tests supported).
- `infra/`: OpenTofu/Terraform for AWS ECS, ECR, DynamoDB.
- `noxfile.py`: Test, lint, format, coverage sessions.
- `pyproject.toml`: Python (>=3.11), deps, pytest, coverage, ruff.
- `Dockerfile*`: Images for verifier, news, and giveaway bots.

## Build, Test, and Development Commands
- Setup: `python -m venv .venv && source .venv/bin/activate && pip install -e .[dev]`
- Run tests (80% min coverage): `nox -s tests` or `pytest -v --cov=.`
- Lint/format: `nox -s lint` (check) and `nox -s format_code` (apply).
- Run locally (example): `DISCORD_TOKEN=... python bot.py`
- Docker (verifier): `docker build -t coc-verifier-bot . && docker run --env DISCORD_TOKEN=... coc-verifier-bot`

## Coding Style & Naming Conventions
- Indentation: 4 spaces; line length: 88 (`[tool.ruff]`).
- Python 3.11 target; prefer type hints.
- Naming: `snake_case` for functions/vars, `CamelCase` for classes, `UPPER_SNAKE_CASE` for constants.
- Imports: sorted by ruff-isort; run `ruff check --fix .` if needed.

## Testing Guidelines
- Frameworks: `pytest`, `pytest-asyncio`; markers configured (`asyncio`).
- File/function names: `tests/test_*.py`, functions `test_*`.
- Coverage: enforced at 80% via nox; HTML report in `htmlcov/`.
- Examples: `pytest -k test_newsbot -m asyncio -vv` to run async news tests.

## Commit & Pull Request Guidelines
- Commits: short, imperative subject (e.g., "Fix logging format").
- Include scope when helpful (e.g., `giveaway:`). Keep related changes together.
- Before PR: `nox -s lint tests` must pass; update tests/docs when changing behavior.
- PRs: clear description, linked issues, env/config notes (`DISCORD_TOKEN`, AWS vars), and screenshots/logs for bot behavior.
- Commit message rules:
  - Separate subject from body with a blank line
  - Limit the subject line to 50 characters
  - Capitalize the subject line
  - Do not end the subject line with a period
  - Use the imperative mood in the subject line
  - Wrap the body at 72 characters
  - Use the body to explain what and why vs. how

## Security & Configuration Tips
- Never commit secrets. Use GitHub Secrets for CI and AWS OIDC role.
- Local env: see `SETUP.md` for required variables (Discord, COC, AWS, DynamoDB).
- Production: infra defined in `infra/`; deployment via `.github/workflows/deploy.yml`.
