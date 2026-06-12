"""Task output caching using file fingerprints.

Cache entries are stored in a platform-appropriate directory via
``platformdirs``.  Each project gets a subdirectory keyed by a hash
of the project root path. Within that, each task has a JSON file
containing fingerprints of its inputs and outputs.

A fast pre-check using ``(mtime, size)`` tuples avoids SHA-256
hashing when files haven't been touched since the last run.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from platformdirs import user_cache_dir

if TYPE_CHECKING:
    from typing import Any


def _cache_root() -> Path:
    """Return the platform-appropriate root cache directory."""
    return Path(user_cache_dir("conda-workspaces"))


def _project_cache_dir(project_root: Path) -> Path:
    """Return the per-project cache directory, creating it if necessary."""
    key = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:16]
    d = _cache_root() / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_file(project_root: Path, task_name: str) -> Path:
    """Return the JSON cache file path for a specific task."""
    return _project_cache_dir(project_root) / f"{task_name}.json"


def _file_stat(path: str) -> tuple[float, int] | None:
    """Return ``(mtime, size)`` for *path*, or None if missing."""
    try:
        st = os.stat(path)
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


def _file_sha256(path: str) -> str:
    """Return the hex SHA-256 digest of the file at *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _expand_globs(patterns: list[str], cwd: Path) -> list[str]:
    """Expand glob patterns relative to *cwd*, return sorted paths."""
    result: set[str] = set()
    for pattern in patterns:
        expanded = glob.glob(str(cwd / pattern), recursive=True)
        result.update(expanded)
    return sorted(result)


def _fingerprint_files(paths: list[str]) -> dict[str, dict[str, Any]]:
    """Build a fingerprint dict: ``{path: {mtime, size, sha256}}``."""
    fp: dict[str, dict[str, Any]] = {}
    for p in paths:
        stat = _file_stat(p)
        if stat is None:
            continue
        fp[p] = {
            "mtime": stat[0],
            "size": stat[1],
            "sha256": _file_sha256(p),
        }
    return fp


def _compute_entry(
    cmd: str | list[str],
    env: dict[str, str],
    input_files: list[str],
    output_files: list[str],
) -> dict[str, Any]:
    """Compute a cache entry from current state."""
    cmd_data = (
        cmd
        if isinstance(cmd, str)
        else json.dumps(cmd, ensure_ascii=True, separators=(",", ":"))
    )
    cmd_hash = hashlib.sha256(cmd_data.encode()).hexdigest()
    env_hash = hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()
    return {
        "cmd_hash": cmd_hash,
        "env_hash": env_hash,
        "inputs": _fingerprint_files(input_files),
        "outputs": _fingerprint_files(output_files),
    }


def is_cached(
    project_root: Path,
    task_name: str,
    cmd: str | list[str],
    env: dict[str, str],
    input_patterns: list[str],
    output_patterns: list[str],
    cwd: Path,
) -> bool:
    """Check whether the task can be skipped (cache hit).

    Returns True only when all of the following hold:

    1. A cache entry exists for the task.
    2. The command and env hashes match.
    3. All input files match by ``(mtime, size)`` -- falling back to
       SHA-256 if the fast check fails.
    4. All output files still exist and match.
    """
    cf = _cache_file(project_root, task_name)
    if not cf.exists():
        return False

    try:
        cached = json.loads(cf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    input_files = _expand_globs(input_patterns, cwd)
    output_files = _expand_globs(output_patterns, cwd)

    current = _compute_entry(cmd, env, input_files, output_files)

    if cached.get("cmd_hash") != current["cmd_hash"]:
        return False
    if cached.get("env_hash") != current["env_hash"]:
        return False

    if not _files_match(cached.get("inputs", {}), current["inputs"]):
        return False
    if not _files_match(cached.get("outputs", {}), current["outputs"]):
        return False

    return True


def _files_match(cached: dict[str, Any], current: dict[str, Any]) -> bool:
    """Compare two fingerprint dicts."""
    if set(cached.keys()) != set(current.keys()):
        return False
    for path, cur in current.items():
        prev = cached.get(path)
        if prev is None:
            return False
        if prev["mtime"] == cur["mtime"] and prev["size"] == cur["size"]:
            continue
        if prev["sha256"] != cur["sha256"]:
            return False
    return True


def save_cache(
    project_root: Path,
    task_name: str,
    cmd: str | list[str],
    env: dict[str, str],
    input_patterns: list[str],
    output_patterns: list[str],
    cwd: Path,
) -> None:
    """Write or update the cache entry for a task."""
    input_files = _expand_globs(input_patterns, cwd)
    output_files = _expand_globs(output_patterns, cwd)
    entry = _compute_entry(cmd, env, input_files, output_files)
    cf = _cache_file(project_root, task_name)
    cf.write_text(json.dumps(entry, indent=2), encoding="utf-8")
