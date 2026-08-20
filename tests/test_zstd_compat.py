import json
import sys
from io import BytesIO

if sys.version_info >= (3, 14):
    from compression import zstd
else:
    from backports import zstd

from conda_metadata_app.zstd_compat import load_zstd_json


def test_loads_json_from_compressed_stream() -> None:
    expected = {
        "packages": {"example-1.0-0.conda": {"name": "example"}},
        "removed": [],
    }
    compressed = zstd.compress(json.dumps(expected).encode())

    actual = load_zstd_json(BytesIO(compressed))

    assert actual == expected
