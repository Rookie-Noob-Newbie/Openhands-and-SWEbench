import os
from pathlib import Path


def _detect_repo_root() -> Path:
    """
    Try to locate the OpenHands repo root by walking up from this file until a
    directory containing a marker file is found. Falls back to the current
    working directory if no marker is detected (e.g., when installed as a
    package).
    """
    current = Path(__file__).resolve()
    markers = ('pyproject.toml', '.git')
    for parent in current.parents:
        if any((parent / marker).exists() for marker in markers):
            return parent
    return Path.cwd()


def get_openhands_temp_dir() -> Path:
    """
    Return a writable temp directory for OpenHands.

    Order of preference:
    1. The OPENHANDS_TMP_DIR environment variable (expanded).
    2. `<repo_root>/tmp` if the repository root can be detected.
    3. `<cwd>/tmp` as a final fallback.
    """
    env_tmp = os.getenv('OPENHANDS_TMP_DIR')
    if env_tmp:
        tmp_dir = Path(env_tmp).expanduser()
    else:
        tmp_dir = _detect_repo_root() / 'tmp'

    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir
