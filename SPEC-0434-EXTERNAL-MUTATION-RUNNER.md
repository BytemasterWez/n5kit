# n5kit: opt-in external strict mutation runner (card #0434; spec v1.0, 3 Nov 2026)

Decision: Wesley, 3 Nov ~17:45 UTC: "Opt-in n5kit option". Lead/architect: DLA lead (Claude Opus 5.5). Builder: Sonnet. Check: Sol.
Context: n5kit.check's mutation loop reruns the whole unit suite per mutant and counts ANY failure as a kill (card #0367, weak evidence);
dla-scanner already has a strict runner (tests/mutate.py: kill only by an assertion in the mutant's named tests) and runs both, so its CI takes ~75 min.

## Requirements
E1. Opt-in only. If the repo has NO `n5kit-check.json`, n5kit.check behaves exactly as today (byte-for-byte same steps and receipt keys).
E2. A committed `n5kit-check.json` at the repo root may declare `{"mutation_runner": ["<argv>", ...], "timeout_s": <int 60..7200>, "reason": "<text>"}`.
    Validate strictly: unknown keys, empty/non-string argv, shell metacharacters as a single string, timeout out of range, or missing reason -> require() fails
    with a clear code (e.g. 'mutation_runner_invalid'). argv is run WITHOUT a shell.
E3. With a declared runner, n5kit.check runs everything it does today (preflight, archive, ruff, secret scan, boundary load+scan, baseline archive tests)
    and then, INSTEAD of its per-mutant loop, runs the runner in the archived tree (cwd = tree, the same env as tests() plus the caller's env for the
    runner's own tooling), with the timeout. rc != 0 or timeout -> require() fails ('mutation_runner_failed'). The receipt records: runner argv, reason,
    rc, duration, the last ~4 KB of output, and `mutations: "external"` (never a fake kill count).
E4. The archive must still include mutations.json if present; n5kit still checks each rule's `old` occurs exactly once in the archive (the cheap
    uniqueness guard) even when the external runner is used.
E5. Tests in n5kit's tests/ (its existing style, offline): no file -> loop path unchanged (receipt keys as before); valid file -> runner run, loop NOT run,
    receipt fields; runner rc 1 -> fail; timeout -> fail; invalid file variants -> fail with the code; a uniqueness failure still fails with the file present.
    Add mutants to n5kit's mutations.json for the new logic (the loop must kill them, as for n5kit itself, which uses no external runner).
E6. Docs: README/CHANGELOG note; the opt-in is reviewable in the project's repo.
## dla-scanner side (separate PR after the n5kit commit exists)
D1. Add `n5kit-check.json`: mutation_runner ["/usr/bin/python3", "tests/mutate.py"], timeout_s 3600, reason "strict runner, #0367/#0434".
D2. Makefile `check`: run `gate` only (n5kit now runs the strict runner inside it), then sql and golden as today; keep `make mutate` as a separate target.
D3. .github/workflows/check.yml: bump N5KIT_REF to the new n5kit commit; nothing else. RUNBOOK/MUTATIONS.md: one-line notes.
D4. Proof: the CI run on the final commit shows the gate time (target ~25 min; report the real number) and the strict runner's "0 survived ... N/N killed" inside it.
