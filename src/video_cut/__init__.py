__version__ = "0.5.0"


def _get_pep440_version(ver: str) -> str:
    """Normalize version string to PEP 440 for python build tools (pip/setuptools)."""
    if ver in ("devel", "dev"):
        return "0.5.0.dev0"
    return ver


__pep440_version__ = _get_pep440_version(__version__)
