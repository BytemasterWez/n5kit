PYTHON ?= python3
N5KIT ?= /home/wez/Work/n5kit
export PYTHONPATH := $(N5KIT):$(CURDIR)
.PHONY: check rehearse run export
check:
	$(PYTHON) -m n5kit.check /tmp/project-check-$$(date +%s%N).json
rehearse:
	N5KIT_REAL=1 $(PYTHON) -m n5kit.run start "$(REHEARSAL_SPEC)" "$(OUT)"
run:
	N5KIT_REAL=1 $(PYTHON) -m n5kit.run start "$(RUN_SPEC)" "$(OUT)"
export:
	git archive --format=tar HEAD > /tmp/project-$$(git rev-parse HEAD).tar
