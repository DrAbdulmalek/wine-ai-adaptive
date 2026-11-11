'''Application recipes (data-only playbooks).'''

from pathlib import Path

from wine_ai.recipes.loader import (
    KNOWN_WINETRICKS_VERBS,
    RecipeError,
    list_recipes,
    load_recipe,
    recipe_finding,
    record_test_outcome,
)

RECIPES_DIR = Path(__file__).resolve().parent

__all__ = [
    'RECIPES_DIR',
    'KNOWN_WINETRICKS_VERBS',
    'RecipeError',
    'list_recipes',
    'load_recipe',
    'recipe_finding',
    'record_test_outcome',
]
