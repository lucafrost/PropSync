#
# Makefile
# --------
# This Makefile has one target, layers, which will produce a
# .zip file for a Python package, ready to be registered as
# an AWS Lambda Layer.
#
# Add additional packages to the variable `PYTHON_PACKAGES`
# and they will be built automatically.
#
# Note: .zip files produced here are *not* automatically
# registered as Lambda Layers, this is handled by the
# Terraform.
#

PYTHON_PACKAGES = xmltodict webflow

.PHONY: layers
layers:
	@for layer in $(PYTHON_PACKAGES); do \
  		mkdir -p build/python; \
  		python -m pip install $$layer --target ./build/python --quiet; \
  		cd build && zip -r ../$$layer\_layer.zip python -x '*__pycache__*' && cd ..; \
  		rm -rf build; \
  		echo "Created AWS Lambda Layer: $$layer @ $$layer\_layer.zip"; \
  	done