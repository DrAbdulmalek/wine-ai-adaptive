"""Tests for recipe loading and validation."""

import json

import pytest

from wine_ai.recipes import RECIPES_DIR, RecipeError, list_recipes, load_recipe


def test_bundled_recipes_are_valid():
    recipes = list_recipes(RECIPES_DIR)
    names = {r["name"] for r in recipes}
    assert "trados-studio" in names
    assert "memoq" in names


def test_load_recipe_missing_file(tmp_path):
    with pytest.raises(RecipeError):
        load_recipe(tmp_path / "nope.json")


def test_load_recipe_rejects_unknown_keys(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "name": "x", "app": "y", "dependencies": [], "notes": "n",
        "shell_command": "rm -rf /",
    }))
    with pytest.raises(RecipeError):
        load_recipe(bad)


def test_load_recipe_rejects_missing_keys(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "x"}))
    with pytest.raises(RecipeError):
        load_recipe(bad)
