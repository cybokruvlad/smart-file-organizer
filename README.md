# Smart File Organizer

A Python tool to organize files by type, preview planned moves, detect duplicate
file contents using SHA-256, and export JSON reports.

## Features

- Preview planned file moves.
- Organize files by extension.
- Avoid overwriting existing destination files.
- Detect duplicate file contents.
- Export operation reports as JSON.
- Automated tests with pytest and linting with Ruff.

## Development

Install development dependencies:

    .\.venv\Scripts\python.exe -m pip install -e ".[dev]"

Run tests:

    .\.venv\Scripts\python.exe -m pytest -v

Check code quality:

    .\.venv\Scripts\ruff.exe check .

## Safety

Test with a disposable directory first. Review planned actions before
executing them. Duplicate detection does not delete files.
