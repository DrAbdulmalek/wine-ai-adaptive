"""Load and validate application recipes (JSON playbooks).

A recipe describes how to prepare a bottle for a known Windows
application: suggested Wine version, runtime dependencies to install
via winetricks, and installation notes. Recipes are data only; they
never contain arbitrary shell commands.
"""

import json
from pathlib import Path

REQUIRED_KEYS = {"name", "app", "dependencies", "notes"}
OPTIONAL_KEYS = {"wine_version_suggestion", "winetricks_verbs", "steps"}
ALLOWED_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS


class RecipeError(Exception):
    """Raised when a recipe file is missing, malformed, or unsafe."""


def load_recipe(path):
    recipe_path = Path(path).expanduser()
    try:
        data = json.loads(
            recipe_path.read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise RecipeError(f"Recipe not found: {recipe_path}") from exc
    except (OSError, ValueError) as exc:
        raise RecipeError(
            f"Recipe file is corrupted: {recipe_path}"
        ) from exc

    if not isinstance(data, dict):
        raise RecipeError("Recipe must be a JSON object")
    missing = REQUIRED_KEYS - set(data)
    if missing:
        raise RecipeError(
            f"Recipe is missing required keys: {sorted(missing)}"
        )
    unsupported = set(data) - ALLOWED_KEYS
    if unsupported:
        raise RecipeError(
            f"Recipe contains unsupported keys: {sorted(unsupported)}"
        )
    for key in ("name", "app", "notes"):
        if not isinstance(data[key], str) or not data[key]:
            raise RecipeError(
                f"Recipe key must be a non-empty string: {key}"
            )
    for key in ("dependencies", "winetricks_verbs", "steps"):
        if key in data and not isinstance(data[key], list):
            raise RecipeError(f"Recipe key must be a list: {key}")
    if "winetricks_verbs" in data and not all(
        isinstance(item, str) for item in data["winetricks_verbs"]
    ):
        raise RecipeError("winetricks_verbs must be a list of strings")
    return data


def list_recipes(directory):
    recipes_dir = Path(directory).expanduser()
    if not recipes_dir.is_dir():
        return []
    found = []
    for item in sorted(recipes_dir.glob("*.json")):
        try:
            found.append(load_recipe(item))
        except RecipeError:
            continue
    return found
