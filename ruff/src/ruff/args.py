# Never linted or formatted: caches, virtualenvs, build output and generated
# Dagger code.
SOURCE_EXCLUDE = [
    "**/__pycache__",
    "**/*.pyc",
    "**/node_modules",
    "**/.venv",
    "**/.tox",
    "**/.nox",
    "**/.git",
    "**/.mypy_cache",
    "**/.pytest_cache",
    "**/.ruff_cache",
    "**/.direnv",
    "**/.devenv",
    "**/dist",
    "**/build",
    "**/*.egg-info",
    "**/sdk",
]

# The files that can pin the ruff version (see `resolve_version`).
VERSION_FILES = ["uv.lock", "ruff.toml", ".ruff.toml", "pyproject.toml"]
