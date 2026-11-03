# Changelog

## Unreleased — 3 November 2026
Opt-in `n5kit-check.json` `mutation_runner` (card #0434): a project may replace the per-mutant loop with its own strict runner; absent file = unchanged behaviour. See README.

## 0.1.0 candidate — 10 October 2026
Shared host reservation/floor guards, transient run lifecycle, owned Docker cleanup, evidence checks and standard project targets. Awaiting real-host acceptance and independent review.

- Native direct-command mode uses the same systemd guards and cleanup; cgroup OOM/peak/task evidence and explicit external-service scope. Native real-host pass/KILL and peak-task follow-up acceptance passed on10October2026.
