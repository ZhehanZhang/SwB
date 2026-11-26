"""Old module paths (origins/, destinations/) kept as aliases of the blocks/ modules for one release."""

from __future__ import annotations

import importlib
import sys


def alias(name: str, target: str, subs: tuple[str, ...] = ()) -> None:
    """Make name the very module target (not a copy), and name.<sub> the module target.<sub>."""
    sys.modules[name] = importlib.import_module(target)
    for sub in subs:
        sys.modules[f"{name}.{sub}"] = importlib.import_module(f"{target}.{sub}")
