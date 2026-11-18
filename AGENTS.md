# Repository Guidelines

## Project Structure

APOD polls Astronet’s Astronomy Picture of the Day index and publishes Russian-language issues to Telegram. `src/main.py` handles polling, HTML parsing, media lookup, and publishing. `src/models.py` defines the Peewee model and initializes SQLite. Parser tests live in `tests/`. Media comes from upstream URLs. The root `Dockerfile` and `docker-compose.yml` define the container setup.

## Build, Test, and Run

- `uv sync --locked --python 3.11` installs the locked runtime and development dependencies. Python 3.11 matches the container.
- `uv run --locked python -m pytest -q` runs the test suite from the repository root.
- `uv run --locked python src/main.py` starts the polling loop locally after configuration.
- `docker compose build` builds the service image. Run `touch db.sqlite` before the first `docker compose up` so Compose can mount the SQLite file.

Copy `.env.demo` to `.env` and set the required values before starting the service. Use a test bot and chat for local runs because the service can publish automatically. Keep tokens and local SQLite data out of commits.

## Coding Style

Use four-space indentation and descriptive `snake_case` names for functions and variables. Use `PascalCase` for classes such as `Issue` and uppercase names for environment variables. Follow the surrounding Python style and keep polling and publishing code compatible with Python 3.11. The repository has no configured formatter or linter.

## Testing

Write pytest cases in `tests/test_*.py` with descriptive `test_*` names. For scraper changes, exercise the real parser with representative HTML and stub HTTP responses so tests run without Astronet or Telegram access. Cover the affected failure case and confirm valid issues still parse. The repository defines no coverage threshold.

## Commits and Pull Requests

Use short, imperative commit subjects, following examples such as `Update HTTP error handling` and `Fix changing issue URL`. In the pull request, explain the behavior change, list the checks you ran, and link the related issue when available. Include a redacted screenshot or sample message when Telegram output changes.
