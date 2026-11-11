"""Strict, data-only application recipe loading and validation.

A recipe is a JSON document describing how a known Windows application
is prepared for a managed Wine bottle. Recipes are pure data:

- no shell commands, URLs, or executable content are accepted;
- winetricks verbs must come from the module allow-list;
- every list element is individually validated;
- a recipe can never declare itself PROVEN; the highest status a
  recipe file may claim is PARTIALLY_PROVEN. Only recorded test
  outcomes (via the knowledge bridge) may raise the evidence level;
- ``last_tested`` is either an ISO date (YYYY-MM-DD, not in the future)
  or an explicit ``null`` meaning "never tested" — the bundled draft
  recipes rely on the null form.

This module deliberately contains no execution logic. Turning a
recipe into actions is the responsibility of a separately reviewed
and separately approved execution engine.
"""

import json
import re
from datetime import date, datetime
from pathlib import Path

from wine_ai.evidence import EvidenceStatus

__all__ = [
    'RecipeError',
    'RECIPE_NAME_PATTERN',
    'KNOWN_WINETRICKS_VERBS',
    'ALLOWED_RECIPE_STATUSES',
    'load_recipe',
    'list_recipes',
    'recipe_finding',
]

RECIPE_NAME_PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')

# Allow-list of winetricks verbs this project is prepared to reason
# about. Anything outside this list is rejected at load time; extending
# the list is a deliberate, reviewable code change.
KNOWN_WINETRICKS_VERBS = frozenset({
    'dotnet48',
    'dotnet45',
    'corefonts',
    'gdiplus',
    'vcrun2019',
    'vcrun2022',
    'win7',
    'win10',
    'winver',
})

# Recipe files describe intent, not results. PROVEN is reserved for
# recorded outcomes.
ALLOWED_RECIPE_STATUSES = frozenset({
    EvidenceStatus.UNPROVEN.value,
    EvidenceStatus.PARTIALLY_PROVEN.value,
    EvidenceStatus.CONTRADICTED.value,
})

MAX_LIST_ITEMS = 32
MAX_ITEM_LENGTH = 128
MAX_NOTE_LENGTH = 1024


class RecipeError(Exception):
    '''Raised when a recipe is missing, malformed, or unsafe.'''


def _validated_string_list(data, key, *, max_items=MAX_LIST_ITEMS):
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, list):
        raise RecipeError(f'Recipe key must be a list: {key}')
    if len(value) > max_items:
        raise RecipeError(f'Recipe list is too long: {key}')
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise RecipeError(
                f'Recipe list items must be non-empty strings: {key}'
            )
        if len(item) > MAX_ITEM_LENGTH:
            raise RecipeError(
                f'Recipe list item is too long in {key}'
            )
    return list(value)


def _validated_date(data, key):
    if key not in data:
        return None
    value = data[key]
    if value is None:
        # Explicit "never tested": honest default for draft recipes.
        return None
    if not isinstance(value, str):
        raise RecipeError(f'Recipe key must be an ISO date string or null: {key}')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise RecipeError(
            f'Recipe key must be an ISO date (YYYY-MM-DD) or null: {key}'
        ) from exc
    if parsed > datetime.now().date():
        raise RecipeError(f'Recipe date is in the future: {key}')
    return value


def load_recipe(path):
    recipe_path = Path(path).expanduser()
    try:
        data = json.loads(recipe_path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise RecipeError(f'Recipe not found: {recipe_path}') from exc
    except (OSError, ValueError) as exc:
        raise RecipeError(
            f'Recipe file is corrupted: {recipe_path}'
        ) from exc

    if not isinstance(data, dict):
        raise RecipeError('Recipe must be a JSON object')

    required = ('name', 'app', 'wine_version_suggestion',
                'dependencies', 'notes', 'compatibility_status')
    missing = [key for key in required if key not in data]
    if missing:
        raise RecipeError(f'Recipe is missing required keys: {missing}')

    allowed = set(required) | {
        'winetricks_verbs', 'steps', 'last_tested',
    }
    unsupported = set(data) - allowed
    if unsupported:
        raise RecipeError(
            f'Recipe contains unsupported keys: {sorted(unsupported)}'
        )

    name = data['name']
    if not isinstance(name, str) or not RECIPE_NAME_PATTERN.fullmatch(name):
        raise RecipeError('Recipe name must be lowercase kebab-case')

    for key in ('app', 'wine_version_suggestion'):
        value = data[key]
        if not isinstance(value, str) or not value.strip():
            raise RecipeError(
                f'Recipe key must be a non-empty string: {key}'
            )
        if len(value) > MAX_ITEM_LENGTH:
            raise RecipeError(f'Recipe key is too long: {key}')

    notes = data['notes']
    if not isinstance(notes, str) or not notes.strip():
        raise RecipeError('Recipe notes must be a non-empty string')
    if len(notes) > MAX_NOTE_LENGTH:
        raise RecipeError('Recipe notes are too long')

    status = data['compatibility_status']
    if status not in ALLOWED_RECIPE_STATUSES:
        raise RecipeError(
            'Recipe compatibility_status must be one of '
            f'{sorted(ALLOWED_RECIPE_STATUSES)}; PROVEN can only be '
            'reached through recorded test outcomes'
        )

    dependencies = _validated_string_list(data, 'dependencies')
    if not dependencies:
        raise RecipeError('Recipe must list at least one dependency')

    verbs = _validated_string_list(data, 'winetricks_verbs')
    if verbs:
        unknown = sorted(set(verbs) - KNOWN_WINETRICKS_VERBS)
        if unknown:
            raise RecipeError(
                'Recipe contains winetricks verbs outside the '
                f'allow-list: {unknown}'
            )

    steps = _validated_string_list(data, 'steps')
    last_tested = _validated_date(data, 'last_tested')

    recipe = dict(data)
    recipe['dependencies'] = dependencies
    if verbs is not None:
        recipe['winetricks_verbs'] = verbs
    if steps is not None:
        recipe['steps'] = steps
    recipe['last_tested'] = last_tested
    return recipe


def list_recipes(directory):
    recipes_dir = Path(directory).expanduser()
    if not recipes_dir.is_dir():
        return []
    found = []
    for item in sorted(recipes_dir.glob('*.json')):
        try:
            found.append(load_recipe(item))
        except RecipeError:
            continue
    return found


def recipe_finding(recipe, *, status, confidence='low'):
    '''Convert a recipe outcome into a knowledge-bridge finding.

    The status must be a valid evidence status. Recipes themselves can
    never be PROVEN, but a recorded test outcome may be, so PROVEN is
    accepted here only when the caller explicitly asserts it. BLOCKED
    is a refusal state, not a recordable outcome, and is rejected.
    '''
    valid_statuses = {item.value for item in EvidenceStatus}
    if status not in valid_statuses:
        raise ValueError('Unsupported evidence status')
    if status == EvidenceStatus.BLOCKED.value:
        raise ValueError('BLOCKED is not a recordable recipe outcome')
    return {
        'category': 'unknown',
        'status': status,
        'confidence': confidence,
        'error_code': 'recipe_' + recipe['name'],
    }
