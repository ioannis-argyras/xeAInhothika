"""Tiny .env loader (no library). Existing environment variables are never replaced."""

import os


def load_env(path=".env"):
    """Read KEY=VALUE lines from `path` into os.environ.

    Blank lines and lines starting with # are ignored. Values may be wrapped
    in single or double quotes. Variables already set are kept. A missing
    file is fine. Returns the list of names that were set.
    """
    set_names = []
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return set_names
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            set_names.append(key)
    return set_names
