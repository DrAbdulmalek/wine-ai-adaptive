"""Tests for strict recipe loading and evidence integration.

Ported to stdlib ``unittest`` (project rule: no external test runner)
from the 2026-11-11 ``feature/bottle-manager-v2`` draft, with regression
coverage for the defect found during integration: the v2 loader rejected
its own bundled recipes because an explicit ``null`` ``last_tested``
failed the ISO-date check, so ``list_recipes`` silently returned nothing.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from wine_ai.recipes import (
    RECIPES_DIR,
    RecipeError,
    list_recipes,
    load_recipe,
    recipe_finding,
)


def _base_recipe(**overrides):
    recipe = {
        "name": "test-app",
        "app": "Test App",
        "wine_version_suggestion": "wine 9.x",
        "winetricks_verbs": ["corefonts"],
        "dependencies": ["Some runtime"],
        "compatibility_status": "UNPROVEN",
        "notes": "Draft for testing.",
    }
    recipe.update(overrides)
    return recipe


class RecipeLoadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, recipe, filename="recipe.json") -> Path:
        path = self.dir / filename
        path.write_text(json.dumps(recipe), encoding="utf-8")
        return path

    # -- bundled recipes (regression: v2 silently dropped both) --------------

    def test_bundled_recipes_are_valid_and_unproven(self):
        recipes = list_recipes(RECIPES_DIR)
        names = {recipe["name"] for recipe in recipes}
        self.assertLessEqual({"trados-studio", "memoq"}, names)
        for recipe in recipes:
            self.assertIn(
                recipe["compatibility_status"],
                ("UNPROVEN", "PARTIALLY_PROVEN", "CONTRADICTED"),
            )
            self.assertNotEqual(recipe["compatibility_status"], "PROVEN")

    def test_bundled_recipes_accept_null_last_tested(self):
        for name in ("trados-studio", "memoq"):
            recipe = load_recipe(RECIPES_DIR / f"{name}.json")
            self.assertIsNone(recipe["last_tested"])

    def test_explicit_null_last_tested_is_never_tested(self):
        recipe = load_recipe(self._write(_base_recipe(last_tested=None)))
        self.assertIsNone(recipe["last_tested"])

    def test_recipe_cannot_declare_proven(self):
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(compatibility_status="PROVEN")))

    def test_winetricks_verbs_allow_list(self):
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(winetricks_verbs=["corefonts", "evil_verb"])))

    def test_list_items_validated_individually(self):
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(dependencies=["ok", ""])))
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(dependencies=["ok", 42])))

    def test_list_length_and_item_length_capped(self):
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(dependencies=["dep"] * 33)))
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(dependencies=["x" * 129])))

    def test_rejects_shell_like_unsupported_keys(self):
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(shell_command="rm -rf /")))

    def test_name_pattern_enforced(self):
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(name="Bad_Name!")))

    def test_future_last_tested_rejected(self):
        future = (date.today() + timedelta(days=2)).isoformat()
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(last_tested=future)))

    def test_non_date_last_tested_rejected(self):
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(last_tested="yesterday")))

    def test_missing_required_key_rejected(self):
        recipe = _base_recipe()
        del recipe["notes"]
        with self.assertRaises(RecipeError):
            load_recipe(self._write(recipe))

    def test_notes_length_capped(self):
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(notes="x" * 1025)))

    def test_empty_dependencies_rejected(self):
        with self.assertRaises(RecipeError):
            load_recipe(self._write(_base_recipe(dependencies=[])))

    def test_missing_file(self):
        with self.assertRaises(RecipeError):
            load_recipe(self.dir / "nope.json")

    def test_corrupted_file_rejected(self):
        path = self.dir / "broken.json"
        path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(RecipeError):
            load_recipe(path)

    def test_list_recipes_missing_directory_is_empty(self):
        self.assertEqual(list_recipes(self.dir / "absent"), [])

    def test_recipe_finding_valid_statuses(self):
        recipe = load_recipe(self._write(_base_recipe()))
        finding = recipe_finding(recipe, status="PARTIALLY_PROVEN")
        self.assertEqual(finding["status"], "PARTIALLY_PROVEN")
        self.assertEqual(finding["error_code"], "recipe_test-app")
        with self.assertRaises(ValueError):
            recipe_finding(recipe, status="NOT_A_STATUS")
        with self.assertRaises(ValueError):
            recipe_finding(recipe, status="BLOCKED")

    def test_recipe_finding_error_code_follows_name(self):
        recipe = load_recipe(self._write(_base_recipe(name="memoq-test")))
        finding = recipe_finding(recipe, status="UNPROVEN")
        self.assertEqual(finding["error_code"], "recipe_memoq-test")


if __name__ == "__main__":
    unittest.main()
