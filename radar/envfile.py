"""Load a local .env file into the process environment.

Values already set on the process win. Nothing in the file is logged.
"""

from collections.abc import MutableMapping
from pathlib import Path


def load_env_file(path: Path, environ: MutableMapping[str, str]) -> None:
    """Fill blank or missing names from path. A missing file does nothing."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or environ.get(name, "").strip():
            continue
        environ[name] = value.strip().strip('"').strip("'")
