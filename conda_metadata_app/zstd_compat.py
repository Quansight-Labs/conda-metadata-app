"""Compatibility helpers for the standard-library Zstandard API."""

import json
import sys
from typing import Any, BinaryIO

if sys.version_info >= (3, 14):
    from compression import zstd
else:
    from backports import zstd


def load_zstd_json(stream: BinaryIO) -> Any:
    """Load JSON from a Zstandard-compressed binary stream."""
    with zstd.open(stream, mode="rb") as reader:
        return json.load(reader)
