"""Preserve build numbers with released conda-lockfiles models."""

from __future__ import annotations

from conda_lockfiles.rattler_lock.v6 import RattlerLockV6, RattlerLockV6Package


# Remove these extensions once the minimum conda-lockfiles version includes
# https://github.com/conda/conda-lockfiles/pull/175.
class WorkspaceLockPackage(RattlerLockV6Package):
    build_number: int | None = None


class WorkspaceLock(RattlerLockV6):
    packages: list[WorkspaceLockPackage]
