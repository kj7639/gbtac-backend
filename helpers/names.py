"""
names.py

Loads a static JSON mapping of sensor codes to human-readable display names
and provides a lookup function used by the graph endpoints that serve the
Ambient Temperature and Wall Temperature dashboards.

Author: Kiera Johnson
"""

from pathlib import Path
import json

sensors: dict[str, str] = {}

path = Path("data/replacement_names.json")
with open(path, "r") as f:
    sensors = json.load(f)


def replace_name(code: str) -> str | bool:
    """
    Retrieves the display name for a given sensor code.

    Args:
        code: Sensor code used as the key in the replacement mapping.

    Returns:
        The human-readable sensor name if found; otherwise False.

    Notes:
        Returns False instead of raising an exception to simplify usage
        in graph endpoints when a mapping does not exist.
    """
    try:
        return sensors[code]
    except KeyError:
        return False