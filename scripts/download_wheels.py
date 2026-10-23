"""Download locked, hash-verified Linux ARM64 wheels for DNS-free Docker builds."""

import hashlib
import platform
import re
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import parse_wheel_filename


def main() -> None:
    platforms = [
        "manylinux_2_28_aarch64",
        "manylinux2014_aarch64",
        "manylinux_2_17_aarch64",
        "manylinux_2_24_aarch64",
    ]
    if platform.machine() in ("x86_64", "AMD64"):
        platforms = [value.replace("aarch64", "x86_64") for value in platforms]
    tags = set(cpython_tags((3, 12), platforms=platforms)) | set(
        compatible_tags((3, 12), platforms=platforms)
    )
    required = set(
        re.findall(
            r"^([\w-]+)==",
            (
                Path("requirements.txt").read_text()
                + (
                    Path("requirements-test.txt").read_text()
                    if Path("requirements-test.txt").exists()
                    else ""
                )
            ),
            re.M,
        )
    )
    locked = tomllib.loads(Path("uv.lock").read_text())["package"]
    root = Path(".wheels")
    root.mkdir(exist_ok=True)
    jobs = []
    for package in locked:
        if package["name"] not in required:
            continue
        for wheel in sorted(
            package.get("wheels", []),
            key=lambda w: (
                not (root / w["url"].split("/")[-1]).exists(),
                "manylinux_2_28" not in w["url"],
            ),
        ):
            filename = wheel["url"].split("/")[-1]
            if parse_wheel_filename(filename)[3] & tags:
                jobs.append((wheel, filename))
                break
        else:
            raise RuntimeError(f"No compatible locked wheel: {package['name']}")
    assert len(jobs) == len(required)

    def download(job):
        wheel, filename = job
        target = root / filename
        expected = wheel["hash"].split(":")[1]
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            return
        for attempt in range(4):
            try:
                response = httpx.get(wheel["url"], timeout=90, follow_redirects=True)
                response.raise_for_status()
                assert hashlib.sha256(response.content).hexdigest() == expected
                target.write_bytes(response.content)
                return
            except (httpx.HTTPError, AssertionError):
                if attempt == 3:
                    raise
                time.sleep(1)

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(download, jobs))
    print(f"Verified {len(jobs)} locked wheels")


if __name__ == "__main__":
    main()
