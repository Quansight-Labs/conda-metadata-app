"""Compatibility helpers for the standard-library Zstandard API."""

import json
import sys
from typing import Any, BinaryIO

if sys.version_info >= (3, 14):
    from compression import zstd
else:
    from backports import zstd


def load_zstd_json(stream: BinaryIO) -> dict[str, Any]:
    """Load JSON from a Zstandard-compressed binary stream."""
    with zstd.open(stream, mode="rb") as reader:
        data = json.load(reader)

    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")

    return data
