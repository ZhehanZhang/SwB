import tomllib
from pathlib import Path

from switchboard.brand import TAGLINE


def test_package_description_matches_tagline():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as file:
        assert tomllib.load(file)["project"]["description"] == TAGLINE["en"]
