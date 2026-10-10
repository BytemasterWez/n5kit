# Project title

Fill SPEC, architecture, decisions, completion plan and tests before building. Use approved synthetic/public fixtures. Pin N5KIT locally; do not modify shared tooling per project. `make check` requires a clean committed tree, Ruff and at least one test. Define mutations.json as targeted changes your tests must reject. Supply approved JSON job specs for `make rehearse`/`make run`; OUT must be a fresh evidence root. `make export` exports HEAD only.

Before enabling CI, set repository variables N5KIT_REPOSITORY and N5KIT_REVISION (full immutable40-character commit) to the approved library repository. CI checks source only and starts no host runtime. Local N5 rehearsal is still required. Choose and pin the development Ruff version after its installation is approved.
