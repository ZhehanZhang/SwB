import json
from importlib.resources import files


TAGLINE: dict[str, str] = json.loads(files(__package__).joinpath("brand.json").read_text(encoding="utf-8"))["tagline"]
