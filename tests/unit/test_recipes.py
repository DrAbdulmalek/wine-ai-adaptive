'''Tests for strict recipe loading and evidence integration.'''

import json

import pytest

from wine_ai.recipes import (
    RECIPES_DIR,
    RecipeError,
    list_recipes,
    load_recipe,
    recipe_finding,
)


def _base_recipe(**overrides):
    recipe = {
        'name': 'test-app',
        'app': 'Test App',
        'wine_version_suggestion': 'wine 9.x',
        'winetricks_verbs': ['corefonts'],
        'dependencies': ['Some runtime'],
        'compatibility_status': 'UNPROVEN',
        'notes': 'Draft for testing.',
    }
    recipe.update(overrides)
    return recipe


def _write(tmp_path, recipe, filename='recipe.json'):
    path = tmp_path / filename
    path.write_text(json.dumps(recipe), encoding='utf-8')
    return path


def test_bundled_recipes_are_valid_and_unproven():
    recipes = list_recipes(RECIPES_DIR)
    names = {r['name'] for r in recipes}
    assert {'trados-studio', 'memoq'} <= names
    for recipe in recipes:
        assert recipe['compatibility_status'] in (
            'UNPROVEN', 'PARTIALLY_PROVEN', 'CONTRADICTED',
        )
        assert recipe['compatibility_status'] != 'PROVEN'


def test_recipe_cannot_declare_proven(tmp_path):
    with pytest.raises(RecipeError):
        load_recipe(_write(tmp_path, _base_recipe(
            compatibility_status='PROVEN',
        )))


def test_winetricks_verbs_allow_list(tmp_path):
    with pytest.raises(RecipeError):
        load_recipe(_write(tmp_path, _base_recipe(
            winetricks_verbs=['corefonts', 'evil_verb'],
        )))


def test_list_items_validated_individually(tmp_path):
    recipe = _base_recipe(dependencies=['ok', ''])
    with pytest.raises(RecipeError):
        load_recipe(_write(tmp_path, recipe))
    recipe = _base_recipe(dependencies=['ok', 42])
    with pytest.raises(RecipeError):
        load_recipe(_write(tmp_path, recipe))


def test_rejects_shell_like_unsupported_keys(tmp_path):
    with pytest.raises(RecipeError):
        load_recipe(_write(tmp_path, _base_recipe(
            shell_command='rm -rf /',
        )))


def test_name_pattern_enforced(tmp_path):
    with pytest.raises(RecipeError):
        load_recipe(_write(tmp_path, _base_recipe(name='Bad_Name!')))


def test_future_last_tested_rejected(tmp_path):
    with pytest.raises(RecipeError):
        load_recipe(_write(tmp_path, _base_recipe(
            last_tested='2099-01-01',
        )))


def test_missing_file(tmp_path):
    with pytest.raises(RecipeError):
        load_recipe(tmp_path / 'nope.json')


def test_recipe_finding_valid_statuses(tmp_path):
    recipe = load_recipe(_write(tmp_path, _base_recipe()))
    finding = recipe_finding(recipe, status='PARTIALLY_PROVEN')
    assert finding['status'] == 'PARTIALLY_PROVEN'
    assert finding['error_code'] == 'recipe_test-app'
    with pytest.raises(ValueError):
        recipe_finding(recipe, status='NOT_A_STATUS')
    with pytest.raises(ValueError):
        recipe_finding(recipe, status='BLOCKED')
