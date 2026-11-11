'''Strict, data-only application recipe loading and validation.

Recipes are data only. This module never initializes Wine, invokes
winetricks, launches subprocesses, or executes recipe steps.

Evidence discipline: recipe metadata and caller-supplied fields are not
proof. Neither ``recipe_finding`` nor ``record_test_outcome`` may emit
PROVEN. The current project has no trusted evidence-verification or
attestation mechanism, and the KnowledgeStore schema has no field for a
verifiable evidence binding. Recipe outcomes therefore remain
UNPROVEN, PARTIALLY_PROVEN, or CONTRADICTED until such a mechanism is
designed and independently reviewed. The generic persistence bridge
also refuses PROVEN records.
'''

import json
import re
from datetime import date, datetime
from pathlib import Path

from wine_ai.evidence import EvidenceStatus
from wine_ai.knowledge.store import SAFE_LABEL

__all__ = [
    'RecipeError',
    'RECIPE_NAME_PATTERN',
    'KNOWN_WINETRICKS_VERBS',
    'ALLOWED_RECIPE_STATUSES',
    'load_recipe',
    'list_recipes',
    'recipe_finding',
]

# Keep the generated RECIPE_<NAME> identifier within KnowledgeStore's
# 64-character UPPER_SNAKE_CASE limit (the prefix consumes 7 characters).
RECIPE_NAME_PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]{0,55}$')
ISO_DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')
RECIPE_ERROR_CODE_PREFIX = 'RECIPE_'

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

ALLOWED_RECIPE_STATUSES = frozenset({
    EvidenceStatus.UNPROVEN.value,
    EvidenceStatus.PARTIALLY_PROVEN.value,
    EvidenceStatus.CONTRADICTED.value,
})

MAX_LIST_ITEMS = 32
MAX_ITEM_LENGTH = 128
MAX_NOTE_LENGTH = 1024
ALLOWED_CONFIDENCE = frozenset({'low', 'medium', 'high'})


class RecipeError(Exception):
    '''Raised when a recipe is missing, malformed, or unsafe.'''


def _reject_duplicate_keys(pairs):
    '''Reject duplicate keys instead of silently accepting the last value.'''
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecipeError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


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
            raise RecipeError(f'Recipe list item is too long in {key}')
    return list(value)


def _validated_date(data, key):
    if key not in data:
        return None
    value = data[key]
    # null means that no compatibility test has been performed.
    if value is None:
        return None
    if not isinstance(value, str) or not ISO_DATE_PATTERN.fullmatch(value):
        raise RecipeError(f'Recipe key must be an ISO date (YYYY-MM-DD): {key}')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise RecipeError(
            f'Recipe key must be a real ISO date (YYYY-MM-DD): {key}'
        ) from exc
    if parsed > datetime.now().date():
        raise RecipeError(f'Recipe date is in the future: {key}')
    return value


def load_recipe(path):
    recipe_path = Path(path).expanduser()
    try:
        data = json.loads(
            recipe_path.read_text(encoding='utf-8'),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except FileNotFoundError as exc:
        raise RecipeError(f'Recipe not found: {recipe_path}') from exc
    except RecipeError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise RecipeError(
            f'Recipe file is corrupted: {recipe_path}'
        ) from exc

    if not isinstance(data, dict):
        raise RecipeError('Recipe must be a JSON object')

    required = (
        'name', 'app', 'wine_version_suggestion',
        'dependencies', 'notes', 'compatibility_status',
    )
    missing = [key for key in required if key not in data]
    if missing:
        raise RecipeError(f'Recipe is missing required keys: {missing}')

    allowed = set(required) | {'winetricks_verbs', 'steps', 'last_tested'}
    unsupported = set(data) - allowed
    if unsupported:
        raise RecipeError(
            f'Recipe contains unsupported keys: {sorted(unsupported)}'
        )

    name = data['name']
    if not isinstance(name, str) or not RECIPE_NAME_PATTERN.fullmatch(name):
        raise RecipeError('Recipe name must be lowercase kebab-case (max 56 chars)')

    for key in ('app', 'wine_version_suggestion'):
        value = data[key]
        if not isinstance(value, str) or not value.strip():
            raise RecipeError(f'Recipe key must be a non-empty string: {key}')
        if len(value) > MAX_ITEM_LENGTH:
            raise RecipeError(f'Recipe key is too long: {key}')

    notes = data['notes']
    if not isinstance(notes, str) or not notes.strip():
        raise RecipeError('Recipe notes must be a non-empty string')
    if len(notes) > MAX_NOTE_LENGTH:
        raise RecipeError('Recipe notes are too long')

    status = data['compatibility_status']
    if not isinstance(status, str) or status not in ALLOWED_RECIPE_STATUSES:
        raise RecipeError(
            'Recipe compatibility_status must be one of '
            f'{sorted(ALLOWED_RECIPE_STATUSES)}; PROVEN can only be '
            'reached through record_test_outcome'
        )

    dependencies = _validated_string_list(data, 'dependencies')
    if not dependencies:
        raise RecipeError('Recipe must list at least one dependency')

    verbs = _validated_string_list(data, 'winetricks_verbs')
    if verbs:
        unknown = sorted(set(verbs) - KNOWN_WINETRICKS_VERBS)
        if unknown:
            raise RecipeError(
                'Recipe contains winetricks verbs outside the allow-list: '
                f'{unknown}'
            )

    steps = _validated_string_list(data, 'steps')
    last_tested = _validated_date(data, 'last_tested')

    recipe = dict(data)
    recipe['dependencies'] = dependencies
    if verbs is not None:
        recipe['winetricks_verbs'] = verbs
    if steps is not None:
        recipe['steps'] = steps
    # Preserve an explicit null: it means "never tested".
    if 'last_tested' in data:
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


def _validated_recipe_name(recipe):
    if not isinstance(recipe, dict):
        raise ValueError('recipe must be a validated recipe dictionary')
    name = recipe.get('name')
    if not isinstance(name, str) or not RECIPE_NAME_PATTERN.fullmatch(name):
        raise ValueError('Invalid recipe name')
    return name


def _validated_evidence_status(status):
    try:
        status_value = EvidenceStatus(status).value
    except (TypeError, ValueError) as exc:
        raise ValueError('Unsupported evidence status') from exc
    if status_value == EvidenceStatus.BLOCKED.value:
        raise ValueError('BLOCKED is not a recordable recipe outcome')
    return status_value


def recipe_finding(recipe, *, status, confidence='low'):
    '''Build a structured finding for explicit persistence via the bridge.

    PROVEN is rejected unconditionally. No trusted evidence verifier,
    attestation binding, or independently reviewed trust configuration
    exists yet. BLOCKED is a refusal state and is not persistable.
    '''
    name = _validated_recipe_name(recipe)
    status_value = _validated_evidence_status(status)
    if status_value == EvidenceStatus.PROVEN.value:
        raise ValueError(
            'recipe_finding cannot emit PROVEN; trusted evidence '
            'verification is not implemented'
        )
    if not isinstance(confidence, str) or confidence not in ALLOWED_CONFIDENCE:
        raise ValueError('Unsupported confidence')

    # KnowledgeStore accepts UPPER_SNAKE_CASE identifiers, not kebab-case.
    error_code = RECIPE_ERROR_CODE_PREFIX + name.replace('-', '_').upper()
    return {
        'category': 'unknown',
        'status': status_value,
        'confidence': confidence,
        'error_code': error_code,
    }


