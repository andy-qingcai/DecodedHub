from pathlib import Path
import tomllib

from decodehub import __version__


def test_package_and_runtime_versions_match_release() -> None:
    root = Path(__file__).resolve().parents[2]
    package_version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    assert package_version == __version__ == "0.3.0"
