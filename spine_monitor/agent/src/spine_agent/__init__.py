"""Local LLM agent for the spine-health monitor."""

import asyncio
import sys
from collections.abc import Sequence

from spine_agent import cli

EXIT_INTERRUPTED = 130


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point; the returned int is the process exit code."""
    try:
        return asyncio.run(cli.amain(argv))
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return EXIT_INTERRUPTED
