# Build the Cellimo documentation site.
#
#   make html      build once, warnings are errors, notebooks execute
#   make clean     drop the build and the execution cache
#   make linkcheck verify external links
#
# The notebook execution cache lives in _build/.jupyter_cache, so an unchanged
# tutorial does not re-run. `make clean` forces it to.

SPHINXOPTS    ?= -W --keep-going
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build

.PHONY: help html clean linkcheck execute

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS)

html:
	@$(SPHINXBUILD) -b html "$(SOURCEDIR)" "$(BUILDDIR)/html" $(SPHINXOPTS)
	@echo "\nBuilt: $(BUILDDIR)/html/index.html"

linkcheck:
	@$(SPHINXBUILD) -b linkcheck "$(SOURCEDIR)" "$(BUILDDIR)/linkcheck" $(SPHINXOPTS)

# Re-run the tutorial against the real Kang 2018 dataset and commit the result.
# Needs the heavy extra: pip install -e '.[docs,docs-data]'
# Takes about six minutes; the dataset is cached after the first download.
execute:
	jupyter nbconvert --to notebook --execute --inplace \
		--ExecutePreprocessor.timeout=2400 tutorial.ipynb
	@echo "\ntutorial.ipynb re-executed — review the diff before committing."

clean:
	rm -rf "$(BUILDDIR)"
