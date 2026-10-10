PYTHON ?= python3
RUFF ?= ruff
export RUFF
.PHONY: check rehearse run export
check:
	$(PYTHON) -m n5kit.check /tmp/n5kit-check-$$(date +%s%N).json
rehearse:
	$(PYTHON) -m tests.acceptance --case "$(CASE)" --image "$(IMAGE)" --out "$(OUT)"
run:
	$(PYTHON) -m n5kit.run start "$(SPEC)" "$(OUT)"
export:
	$(PYTHON) -c 'import pathlib,tempfile; from n5kit import evidence; d=pathlib.Path(tempfile.mkdtemp(prefix="n5kit-export-")); t,r=evidence.archive(pathlib.Path.cwd(),d); evidence.receipt(d/"manifest.json",dict(r,files=evidence.manifest(t))); print(d)'
