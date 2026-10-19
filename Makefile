.PHONY: test python-test

test: python-test

python-test:
	python -m pytest -q --junitxml=build/python-results.xml
