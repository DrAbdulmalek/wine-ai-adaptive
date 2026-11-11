PY ?= python3

.PHONY: test compile clean install editable

## Stdlib-only test run (unittest, no external dependencies)
test:
	PYTHONPATH=src $(PY) -m unittest discover -s tests/unit -p 'test_*.py' -v

compile:
	$(PY) -m compileall -q src tests

clean:
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	rm -rf *.egg-info

install:
	$(PY) -m pip install .

editable:
	$(PY) -m pip install -e .
