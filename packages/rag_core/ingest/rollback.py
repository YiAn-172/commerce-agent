from __future__ import annotations

import argparse
import asyncio
import json

from packages.rag_core.ingest.activate import activate_version
from packages.rag_core.ingest.common import add_common_arguments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roll back RAG aliases to an earlier version")
    add_common_arguments(parser)
    parser.set_defaults(version=None)
    args = parser.parse_args()
    if not args.version:
        parser.error("--version is required for rollback")
    return args


def main() -> None:
    result = asyncio.run(activate_version(parse_args()))
    result["operation"] = "rollback"
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
