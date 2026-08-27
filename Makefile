PY = python3
SRC_DIR = src
BUILD_DIR = dist
ENTRY_MODULE = entry
ENTRY_FUNC = main
ARTIFACT = gamemode.pyz
OUT = $(BUILD_DIR)/$(ARTIFACT)
CHECKSUM = $(BUILD_DIR)/$(ARTIFACT).sha256sum
VERSION_FILE = $(SRC_DIR)/gamemode/__version__.py

# Fixed epoch for reproducible builds (forced, so env overrides can't break determinism):
SOURCE_DATE_EPOCH := 1

# Extract version from pyproject.toml
VERSION := $(shell grep '^version = ' pyproject.toml | cut -d'"' -f2)

# Human-readable timestamp for logging
TIMESTAMP := $(shell date -d "@$(SOURCE_DATE_EPOCH)" -u +%Y-%m-%dT%H:%M:%SZ)

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +; \
	rm -rf \
	$(BUILD_DIR) \
	.pytest_cache \
	.ruff_cache \
	.direnv \
	.pi \
	result* \
	.coverage

build-nix: clean
	@echo "Building $(ARTIFACT) via Nix (version $(VERSION))"
	mkdir -p $(BUILD_DIR)
	nix build . --out-link ./$(OUT)
	cd $(BUILD_DIR) && sha256sum $(ARTIFACT) > $(ARTIFACT).sha256sum
	@echo "Built: $(OUT)"
	@echo "SHA256: $$(cat $(OUT).sha256sum | cut -d' ' -f1)"

configure: clean
	uv venv --clear
	uv sync --frozen

build: configure
	@echo "Building $(ARTIFACT) (version $(VERSION))"
	@echo "SOURCE_DATE_EPOCH: $(SOURCE_DATE_EPOCH) ($(TIMESTAMP))"
	mkdir -p $(BUILD_DIR)

	# Inject version into constants.py
	sed -i 's/^__version__ = .*/__version__ = "$(VERSION)"/' $(VERSION_FILE)

	# Create staging directory for deterministic build
	# Copy contents of src/ directly into staging (not src/ itself)
	rm -rf $(BUILD_DIR)/staging
	mkdir -p $(BUILD_DIR)/staging
	cp -r $(SRC_DIR)/* $(BUILD_DIR)/staging/

	# Create __main__.py for zipapp entry point (overwrite if exists)
	echo "from entry import main; main()" > $(BUILD_DIR)/staging/__main__.py

	# Normalize timestamps on ALL files in staging directory
	# This is crucial for bitwise determinism
	find $(BUILD_DIR)/staging -exec touch -d "@$(SOURCE_DATE_EPOCH)" {} \;

	# Create the zip archive deterministically
	# -X: strip extra file attributes
	# -q: quiet mode
	# Using 'find | LC_ALL=C sort' ensures consistent file ordering across systems
	cd $(BUILD_DIR)/staging && \
		find . -type f | LC_ALL=C sort | \
		zip -X -q -@ ../archive.zip

	# Prepend shebang to create executable pyz
	echo '#!/usr/bin/env python3' > $(OUT)
	cat $(BUILD_DIR)/archive.zip >> $(OUT)
	chmod +x $(OUT)

	# Generate SHA256 checksum file for verification (basename only for portability)
	cd $(BUILD_DIR) && sha256sum $(ARTIFACT) > $(ARTIFACT).sha256sum

	# Cleanup staging and intermediate files
	rm -rf $(BUILD_DIR)/staging $(BUILD_DIR)/archive.zip

	@echo "Built: $(OUT)"
	@echo "SHA256: $$(cat $(OUT).sha256sum | cut -d' ' -f1)"

install: build
	@cd $(BUILD_DIR) && sha256sum -c $(ARTIFACT).sha256sum
	@if [ -d "$$HOME/.local/bin/scripts/" ]; then \
		INSTALL_DIR="$$HOME/.local/bin/scripts"; \
	else \
		mkdir -p "$$HOME/.local/bin"; \
		INSTALL_DIR="$$HOME/.local/bin"; \
	fi; \
	cp -f $(OUT) $(OUT).sha256sum "$$INSTALL_DIR/"; \
	chmod +x "$$INSTALL_DIR/$(ARTIFACT)"; \
	ln -sf "$$INSTALL_DIR/$(ARTIFACT)" "$$HOME/.local/bin/gamemode"; \
	echo "Installed to $$INSTALL_DIR/$(ARTIFACT)"

test:
	uv run pytest --tb=short --cov=src --cov-report=term-missing --cov-branch

lint:
	uv run ty check ./src ./tests; \
		uv run ruff check ./src ./tests --fix; \
		uv run pyright

prettier:
	prettier -c -w *.md

format: prettier
	uv run ruff check --select I ./src ./tests --fix; \
	uv run ruff format ./src ./tests

radon-cc:
	@echo "Running Radon Cyclomatic-Complexity check..."
	uv run radon cc ./src --show-complexity --min B -a

radon-mi:
	@echo "Running Radon Maintainability-Index check..."
	uv run radon mi ./src --show --min A --max C --sort

radon: radon-cc radon-mi

quality: lint format

ci: configure test lint build

ci-nix: lint test build-nix

all: build install

.PHONY: build-nix build install test lint prettier format radon-cc radon-mi radon quality clean all configure ci ci-nix
.SILENT: build-nix build install test lint prettier format radon-cc radon-mi radon quality clean all configure ci ci-nix
