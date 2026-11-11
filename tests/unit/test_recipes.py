'''Stdlib tests for strict recipe loading and KnowledgeStore integration.'''

import json
import tempfile
import unittest
from pathlib import Path

from wine_ai.knowledge.bridge import persist_findings
from wine_ai.knowledge.store import KnowledgeStore
from wine_ai.recipes import (
    RECIPES_DIR,
    RecipeError,
    list_recipes,
    load_recipe,
    recipe_finding,
)


def base_recipe(**overrides):
    recipe = {
        'name': 'test-app',
        'app': 'Test App',
        'wine_version_suggestion': 'wine 9.x',
        'winetricks_verbs': ['corefonts'],
        'dependencies': ['Some runtime'],
        'compatibility_status': 'UNPROVEN',
        'last_tested': None,
        'notes': 'Draft for testing.',
    }
    recipe.update(overrides)
    return recipe



class RecipeLoaderTests(unittest.TestCase):
    def write_recipe(self, directory, recipe, filename='recipe.json'):
        path = Path(directory) / filename
        path.write_text(json.dumps(recipe), encoding='utf-8')
        return path

    def test_bundled_recipes_are_valid_and_unproven(self):
        recipes = list_recipes(RECIPES_DIR)
        by_name = {recipe['name']: recipe for recipe in recipes}
        self.assertTrue({'trados-studio', 'memoq'} <= set(by_name))
        for name in ('trados-studio', 'memoq'):
            self.assertEqual(by_name[name]['compatibility_status'], 'UNPROVEN')
            self.assertIsNone(by_name[name]['last_tested'])

    def test_null_last_tested_is_valid_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_recipe(directory, base_recipe(last_tested=None))
            recipe = load_recipe(path)
        self.assertIn('last_tested', recipe)
        self.assertIsNone(recipe['last_tested'])

    def test_real_iso_date_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_recipe(
                directory, base_recipe(last_tested='2026-01-15')
            )
            recipe = load_recipe(path)
        self.assertEqual(recipe['last_tested'], '2026-01-15')

    def test_noncanonical_or_future_dates_are_rejected(self):
        for invalid in ('20260115', '2026-02-30', '2099-01-01', 20260115):
            with self.subTest(value=invalid):
                with tempfile.TemporaryDirectory() as directory:
                    path = self.write_recipe(
                        directory, base_recipe(last_tested=invalid)
                    )
                    with self.assertRaises(RecipeError):
                        load_recipe(path)

    def test_recipe_cannot_declare_proven(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_recipe(
                directory, base_recipe(compatibility_status='PROVEN')
            )
            with self.assertRaises(RecipeError):
                load_recipe(path)

    def test_winetricks_verbs_allow_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_recipe(
                directory,
                base_recipe(winetricks_verbs=['corefonts', 'evil_verb']),
            )
            with self.assertRaises(RecipeError):
                load_recipe(path)

    def test_each_list_item_is_validated(self):
        for invalid in (['ok', ''], ['ok', 42], ['x' * 129]):
            with self.subTest(value=invalid):
                with tempfile.TemporaryDirectory() as directory:
                    path = self.write_recipe(
                        directory, base_recipe(dependencies=invalid)
                    )
                    with self.assertRaises(RecipeError):
                        load_recipe(path)

    def test_unknown_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_recipe(
                directory, base_recipe(shell_command='not-allowed')
            )
            with self.assertRaises(RecipeError):
                load_recipe(path)

    def test_duplicate_json_keys_are_rejected(self):
        raw = (
            '{"name":"test-app","name":"other-app",'
            '"app":"Test","wine_version_suggestion":"wine 9",'
            '"dependencies":["runtime"],"notes":"draft",'
            '"compatibility_status":"UNPROVEN"}'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'duplicate.json'
            path.write_text(raw, encoding='utf-8')
            with self.assertRaises(RecipeError):
                load_recipe(path)

    def test_invalid_name_and_missing_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_recipe(
                directory, base_recipe(name='Bad_Name!')
            )
            with self.assertRaises(RecipeError):
                load_recipe(path)
            with self.assertRaises(RecipeError):
                load_recipe(Path(directory) / 'missing.json')


class RecipeFindingEvidenceTests(unittest.TestCase):
    """recipe_finding can never mint PROVEN, whatever the caller passes."""

    def setUp(self):
        self.recipe = {
            'name': 'trados-studio',
            'compatibility_status': 'UNPROVEN',
        }

    def test_proven_is_rejected_unconditionally(self):
        for confidence in ('low', 'medium', 'high'):
            with self.subTest(confidence=confidence):
                with self.assertRaises(ValueError):
                    recipe_finding(
                        self.recipe,
                        status='PROVEN',
                        confidence=confidence,
                    )

    def test_non_proven_statuses_are_allowed(self):
        for status in ('UNPROVEN', 'PARTIALLY_PROVEN', 'CONTRADICTED'):
            with self.subTest(status=status):
                finding = recipe_finding(self.recipe, status=status)
                self.assertEqual(finding['status'], status)
                self.assertEqual(finding['error_code'], 'RECIPE_TRADOS_STUDIO')

    def test_blocked_and_invalid_statuses_are_rejected(self):
        for status in ('BLOCKED', 'NOT_A_STATUS', None, 42):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    recipe_finding(self.recipe, status=status)

    def test_invalid_confidence_and_names_are_rejected(self):
        with self.assertRaises(ValueError):
            recipe_finding(self.recipe, status='UNPROVEN', confidence='certain')
        with self.assertRaises(ValueError):
            recipe_finding(self.recipe, status='UNPROVEN', confidence=[])
        with self.assertRaises(ValueError):
            recipe_finding({'name': 'Bad_Name'}, status='UNPROVEN')
        with self.assertRaises(ValueError):
            recipe_finding({'name': 'a' * 57}, status='UNPROVEN')


class RecipeEvidenceIntegrationTests(unittest.TestCase):
    def test_non_proven_test_observation_persists_through_knowledge_bridge(self):
        recipe = {
            'name': 'trados-studio',
            'compatibility_status': 'UNPROVEN',
        }
        finding = recipe_finding(recipe, status='PARTIALLY_PROVEN')
        self.assertEqual(finding['error_code'], 'RECIPE_TRADOS_STUDIO')

        with tempfile.TemporaryDirectory() as directory:
            store = KnowledgeStore(Path(directory) / 'knowledge.sqlite3')
            record_ids = persist_findings(
                store,
                [finding],
                wine_version='9.0',
                distro='Manjaro',
            )
            self.assertEqual(len(record_ids), 1)
            rows = store.list_records()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['error_code'], 'RECIPE_TRADOS_STUDIO')
            self.assertEqual(rows[0]['evidence_status'], 'PARTIALLY_PROVEN')
            self.assertEqual(rows[0]['confidence'], 'high')

    def test_recipe_finding_cannot_emit_proven(self):
        recipe = {'name': 'trados-studio', 'compatibility_status': 'UNPROVEN'}
        with self.assertRaises(ValueError):
            recipe_finding(recipe, status='PROVEN', confidence='high')

    def test_empty_test_list_is_independently_rejected(self):
        recipe = {'name': 'memoq', 'compatibility_status': 'UNPROVEN'}
        outcome = base_outcome(tests_performed=[])
        with self.assertRaisesRegex(ValueError, 'non-empty list'):
            record_test_outcome(recipe, **outcome)


if __name__ == '__main__':
    unittest.main()
