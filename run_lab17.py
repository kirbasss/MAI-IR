"""Build the test image, run every lab's automated tests, save an auditable log."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from pymongo import MongoClient
from pymongo.errors import PyMongoError

ROOT = Path(__file__).resolve().parent


def run(command: list[str], log: Path, env: dict[str, str] | None = None) -> tuple[int, float]:
    start = time.perf_counter()
    with log.open("wb") as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, env=env)
    return result.returncode, time.perf_counter() - start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "doc/lab17")
    parser.add_argument("--rebuild-image", action="store_true",
                        help="rebuild the test image even when it is already available")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    config = subprocess.run(["docker", "compose", "--profile", "tests", "config", "--format", "json"],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    project = json.loads(config.stdout)["name"]
    image = f"{project}-tokenizer-tests:latest"
    image_exists = subprocess.run(["docker", "image", "inspect", image], cwd=ROOT,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if args.rebuild_image or not image_exists:
        build_code, build_seconds = run(
            ["docker", "compose", "--profile", "tests", "build", "tokenizer-tests"],
            output / "build.txt",
        )
        if build_code:
            print(f"Test image build failed; see {output/'build.txt'}", file=sys.stderr)
            return build_code
    else:
        build_seconds = 0.0
        (output / "build.txt").write_text(f"Using existing test image: {image}\n", encoding="utf-8")

    container = "mai-ir-lab17-mongo-" + uuid.uuid4().hex[:12]
    mongo_log = output / "mongo.txt"
    started = subprocess.run(
        ["docker", "run", "--rm", "-d", "--name", container,
         "--publish", "127.0.0.1::27017", "mongo:8.0"],
        cwd=ROOT, capture_output=True, text=True,
    )
    mongo_log.write_text(started.stdout + started.stderr, encoding="utf-8")
    if started.returncode:
        print(f"Test MongoDB could not start; see {mongo_log}", file=sys.stderr)
        return started.returncode
    try:
        port_result = subprocess.run(["docker", "port", container, "27017/tcp"],
                                     cwd=ROOT, capture_output=True, text=True, check=True)
        port = int(port_result.stdout.strip().rsplit(":", 1)[1])
        mongo_uri = f"mongodb://127.0.0.1:{port}/?directConnection=true"
        deadline = time.monotonic() + 45
        while True:
            try:
                with MongoClient(mongo_uri, serverSelectionTimeoutMS=1000) as client:
                    client.admin.command("ping")
                break
            except PyMongoError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Test MongoDB did not become ready")
                time.sleep(0.5)
        with mongo_log.open("a", encoding="utf-8") as stream:
            stream.write(f"Ready on 127.0.0.1:{port}; isolated ephemeral container\n")
        env = os.environ.copy()
        env["MAI_IR_TEST_MONGO_URI"] = mongo_uri
        test_code, test_seconds = run(
            [sys.executable, "run_tests.py", "--lab", "all"], output / "tests.txt", env=env,
        )
    finally:
        stopped = subprocess.run(["docker", "stop", container], cwd=ROOT,
                                 capture_output=True, text=True)
        with mongo_log.open("a", encoding="utf-8") as stream:
            stream.write(f"Stopped: {stopped.returncode == 0}\n")
    text = (output / "tests.txt").read_text(encoding="utf-8", errors="replace")
    cpp = re.search(r"100% tests passed, 0 tests failed out of (\d+)", text)
    python = re.search(r"Ran (\d+) tests in [\d.]+s", text)
    skipped = re.search(r"OK \(skipped=(\d+)\)", text)
    if test_code or not cpp or not python:
        print(f"Test suite failed or log is incomplete; see {output/'tests.txt'}", file=sys.stderr)
        return test_code or 1

    # Hash inputs that define the Lab 17 addition. The full source tree is
    # recorded by the branch/commit and the individual test names in tests.txt.
    inputs = [ROOT / name for name in (
        "CMakeLists.txt", "run_tests.py", "run_lab17.py",
        "tests/cpp/unit_test_lab17.cpp", "tests/python/test_lab17.py",
    )]
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    summary = {
        "revision_base": revision,
        "cpp_ctest": int(cpp.group(1)),
        "python_unittest": int(python.group(1)),
        "python_skipped": int(skipped.group(1)) if skipped else 0,
        "image_build_seconds": round(build_seconds, 3),
        "image": image,
        "image_reused": image_exists and not args.rebuild_image,
        "suite_wall_seconds": round(test_seconds, 3),
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in inputs},
        "test_mongodb": "isolated ephemeral mongo:8.0 container",
        "logs": {"build": "build.txt", "mongo": "mongo.txt", "tests": "tests.txt"},
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    print(f"Passed: {summary['cpp_ctest']} CTest, {summary['python_unittest']} Python "
          f"({summary['python_skipped']} skipped). Logs: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
