# Scope log — n5kit

2026-10-11T11:06:35.275629+01:00 — Wesley via lead #0232 10:51; scope ruling10:55 and binding notes10:57. Initial declaration only; does not change existing runtime/pins or authorize sources, credentials, data acquisition or release. Builder Codex | GPT-6 Astra | N5. Independent policy derivation review pending.

Boundary derives from the exact local canon lines in BOUNDARY.yaml. Other project roots and personal-data roots are never mounted. Never-store lists are project-specific: contact fields prohibited for FedSpend/Calder; empty for FirstPass (normalization may legitimately preserve those fields), CypherMesh (opaque content) and County/n5kit (no new field ban invented). Existing project privacy/source rights still apply.

Network: default deny-all; FedSpend declares only official acquisition hosts from SOURCES. This is a ceiling, NOT acquisition permission; per-unit rights/admission remain mandatory. Network-enabled jobs can reach dynamically constructed destinations and abstract Unix sockets; literal scanning is not a firewall. Trusted sockets default empty; later grants require a scope-log decision and acknowledge host-control capability.

Database allowlist is empty: no new network/database credential access granted. Existing local-file engines remain inside their own project scope. No credential values are recorded. County exchange inputs remain empty until a separately approved publication; Calder uses only its already copied immutable in-project snapshot, not the FedSpend export root. FirstPass/CypherMesh current qualification/runtime is not repinned.

Explicit scan exemptions (review required; no claim these files were checked by the literal scan):
- *.md: canon, operational history and policy explanations; not executable input
- tests/**: synthetic negative fixtures; independent unit checks still run
- mutations.json: explicit negative source mutations
- .github/**: CI metadata, not job networking
- n5kit/boundary.py: scanner implementation necessarily contains host/connection matching patterns; separately unit/mutation checked
- templates/**: policy templates with negative/example paths, not executable project jobs

Legacy executable findings, if any, are reported at adoption, not auto-exempted or silently repaired by this task. This policy does not claim a current legacy tree passes the new gate.

## M1 correction and capability disposition — 11 October 2026

Checker11:16:56 and lead11:18:06 require protected control receipts and an externally held digest. Config, START and timer receipt files are overmounted read-only after writable mounts in both job sandboxes and in the native cleanup callback. submit hashes the exact config bytes; systemd holds the digest in N5KIT_CONFIG_SHA256 for ExecStopPost, and the launcher fallback passes its local digest. cleanup refuses mismatched/missing digest or config symlink before decoding the config or invoking any callback/resource release. A refusal retains the reservation for an operator. The job's cleared environment cannot replace the manager's digest. The failed candidate16eb24e and its original receipts remain preserved.

Precise accepted Docker exception: DAC_OVERRIDE is retained for the existing root-image contract to write uid1000-owned0700 output; all other capabilities are dropped, no-new-privileges remains enabled, root filesystem and source/input mounts are read-only, and mounts are policy-checked. It bypasses permissions only on already visible mounts, not read-only mount enforcement. Accepted by fable-county11:16:56 and lead11:18:06. No general privileged mode or additional capability is granted.

The URL parser checks HTTP(S) destinations; the bare-host regex is intentionally limited to its enumerated TLDs (not all .uk/.dev/.ai strings). Static literal evidence is not exhaustive network enforcement. No proof or adoption before exact G4 CLEAR.
