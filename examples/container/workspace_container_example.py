"""Show that copied application source runs with the image's activation."""

from __future__ import annotations

import json
import os
import sys


def main() -> None:
    """Report the runtime prefix, activation values, and application arguments."""
    print(
        json.dumps(
            {
                "prefix": sys.prefix,
                "message": os.environ["WORKSPACE_MESSAGE"],
                "hook": os.environ["WORKSPACE_HOOK"],
                "arguments": sys.argv[1:],
            }
        )
    )


if __name__ == "__main__":
    main()
