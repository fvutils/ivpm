
IVPM_DIR:=$(dir $(abspath $(lastword $(MAKEFILE_LIST))))
ifeq (,$(PACKAGES_DIR))
  PACKAGES_DIR := $(IVPM_DIR)/packages
endif

export PACKAGES_DIR

$(PACKAGES_DIR)/python :
	mkdir -p $(PACKAGES_DIR)
	python3 -m venv $(PACKAGES_DIR)/python
	$(PACKAGES_DIR)/python/bin/python3 -m pip install --upgrade pip
	$(PACKAGES_DIR)/python/bin/python3 -m pip install -r $(IVPM_DIR)/requirements.txt
	PYTHONPATH=$(IVPM_DIR)/src $(PACKAGES_DIR)/python/bin/python3 -m ivpm update

pdf : $(PACKAGES_DIR)/python
	$(PACKAGES_DIR)/python/bin/sphinx-build -M latexpdf \
		$(IVPM_DIR)/doc/source \
		build

html : $(PACKAGES_DIR)/python
	$(PACKAGES_DIR)/python/bin/sphinx-build -M html \
		$(IVPM_DIR)/doc/source \
			build
	cp $(IVPM_DIR)/src/ivpm/schema/ivpm.json $(IVPM_DIR)/build/html/ivpm.json

clean :
	rm -rf build 

