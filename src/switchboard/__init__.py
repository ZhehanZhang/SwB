from importlib.metadata import PackageNotFoundError, version


def installed_version() -> str | None:
    try:
        return version("swb-one")
    except PackageNotFoundError:
        return None


__version__ = installed_version() or "unknown"
USER_AGENT = f"SwB/{__version__}"
