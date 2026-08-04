from __future__ import annotations

import subprocess
import sys

FORBIDDEN_RUNTIME_PACKAGES = (
    "cuda-bindings",
    "cuda-pathfinder",
    "cuda-toolkit",
    "nvidia-",
    "triton v",
)


def main() -> int:
    result = subprocess.run(
        [
            "uv",
            "export",
            "--project",
            "apps/api",
            "--locked",
            "--no-dev",
            "--extra",
            "cpu",
            "--no-emit-project",
            "--no-header",
            "--no-annotate",
            "--no-hashes",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode

    forbidden_lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if any(package in line.lower() for package in FORBIDDEN_RUNTIME_PACKAGES)
    ]
    if forbidden_lines:
        print("Linux CPU dependencies include accelerator runtime packages:", file=sys.stderr)
        for line in forbidden_lines:
            print(f"- {line}", file=sys.stderr)
        return 1

    torch_lines = [line for line in result.stdout.splitlines() if line.startswith("torch==")]
    linux_cpu_lines = [
        line
        for line in torch_lines
        if "+cpu" in line and "sys_platform != 'darwin'" in line
    ]
    if not linux_cpu_lines:
        print("CPU dependency export does not pin a CPU-only torch build", file=sys.stderr)
        return 1

    print("Linux CPU dependency contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
