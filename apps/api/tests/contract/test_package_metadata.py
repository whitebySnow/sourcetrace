from __future__ import annotations

import tomllib
from pathlib import Path


def test_package_readme_does_not_escape_project_directory() -> None:
    project_directory = Path(__file__).parents[2]
    pyproject = tomllib.loads((project_directory / "pyproject.toml").read_text(encoding="utf-8"))
    readme = pyproject["project"]["readme"]

    if isinstance(readme, str):
        readme_path = (project_directory / readme).resolve()
        assert readme_path.is_relative_to(project_directory.resolve())
        assert readme_path.is_file()
        return

    assert readme == {
        "text": "SourceTrace API and worker package.",
        "content-type": "text/markdown",
    }
