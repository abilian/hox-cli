# hox-cli documentation, built with Zensical

ZENSICAL := uvx zensical

.PHONY: help build serve deploy clean

help:
	@echo "hox-cli documentation (Zensical)"
	@echo ""
	@echo "  make build    - Build the site into site/"
	@echo "  make serve    - Start a local server that rebuilds on change"
	@echo "  make deploy   - Build, then deploy site/ with Hop3 (hop3.toml)"
	@echo "  make clean    - Remove site/"

build:
	$(ZENSICAL) build --clean

serve:
	$(ZENSICAL) serve --open

deploy: build
	hop3 deploy

clean:
	rm -rf site/
