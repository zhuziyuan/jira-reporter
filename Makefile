.PHONY: test coverage lint

project_name = reporter
coverage_options = --include='$(project_name)/*' --omit='$(project_name)/test/*,$(project_name)/config.py,*__init__.py'

test:
	py.test -x $(project_name)

coverage:
	rm -f .coverage*
	rm -rf htmlcov/*
	coverage run -p -m py.test -x $(project_name)
	coverage combine
	coverage html -d htmlcov $(coverage_options)
	coverage xml -i
	coverage report $(coverage_options)

lint:
	pep8 $(project_name)/; pylint $(project_name)/

check-php:
	python $(project_name)/bin/check_php_log.py

sandbox:
	python $(project_name)/bin/sandbox.py 2>&1 | less