"""Build-time only: download and verify a pinned Temporal test server."""

import hashlib
import io
import platform
import sys
import tarfile
import urllib.request
from pathlib import Path

VERSION = "1.40.0"
CHECKSUMS = {
    "linux_amd64": "524465a96274831e004326f6e2f2a5f3ef9aaed72c2069cf16c1d64b8b1ec3ca",
    "linux_arm64": "1d712f6f1de8a97454185d335752d0f9ea8267797c340b55569312251f6670e9",
    "macOS_arm64": "483d6b6d75979b78efa6a93ee159e8eb3232e919fe7d3576fce6e90a167804ec",
}


def main() -> None:
    system = {"Darwin": "macOS", "Linux": "linux"}[platform.system()]
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64"}[platform.machine()]
    target = f"{system}_{arch}"
    name = f"temporal-test-server_{VERSION}_{target}.tar.gz"
    url = f"https://github.com/temporalio/sdk-java/releases/download/v{VERSION}/{name}"
    with urllib.request.urlopen(url, timeout=120) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != CHECKSUMS[target]:
        raise RuntimeError("Test server checksum mismatch")
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else ".tools/temporal-test-server")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        member = next(m for m in bundle.getmembers() if m.name.endswith("temporal-test-server"))
        source = bundle.extractfile(member)
        assert source is not None
        destination.write_bytes(source.read())
    destination.chmod(0o755)
    print(f"Verified Temporal test server {VERSION} ({target}): {destination}")


if __name__ == "__main__":
    main()
