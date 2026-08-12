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

.PHONY: help html clean linkcheck

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS)

html:
	@$(SPHINXBUILD) -b html "$(SOURCEDIR)" "$(BUILDDIR)/html" $(SPHINXOPTS)
	@echo "\nBuilt: $(BUILDDIR)/html/index.html"

linkcheck:
	@$(SPHINXBUILD) -b linkcheck "$(SOURCEDIR)" "$(BUILDDIR)/linkcheck" $(SPHINXOPTS)

clean:
	rm -rf "$(BUILDDIR)"
