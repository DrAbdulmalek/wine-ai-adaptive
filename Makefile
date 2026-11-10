PY ?= python3

.PHONY: test test-pytest compile clean install editable

## Stdlib-only test run (no pytest required)
test:
	PYTHONPATH=src $(PY) -m unittest discover -s tests/unit -p 'test_*.py' -v

## Same suite through pytest (pip install -e ".[dev]" first)
test-pytest:
	$(PY) -m pytest tests/ -q

compile:
	$(PY) -m compileall -q src tests

clean:
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	rm -rf .pytest_cache
	rm -rf *.egg-info

install:
	$(PY) -m pip install .

editable:
	$(PY) -m pip install -e .
