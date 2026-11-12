# CAT compatibility evidence log

**Companion to:** [`docs/cat-tools/TEST_MATRIX.md`](cat-tools/TEST_MATRIX.md) (criteria) and
[`docs/cat-tools/EVIDENCE_POLICY.md`](cat-tools/EVIDENCE_POLICY.md) (rules).
This file is the fill-in record of actual evidence. Until a row here is filled
with reproducible results, every compatibility claim stays `UNPROVEN`.

## Current verdict

| Application | Recipe status | Last tested | Established compatibility? |
|---|---|---|---|
| Trados Studio | UNPROVEN | null | No |
| memoQ Translator Pro | UNPROVEN | null | No |

What exists today (and what it does **not** prove):

- Recipe files and dependency hypotheses ship in the package — a recipe is a
  hypothesis, never evidence.
- Common error signatures are classified (for example
  `0x80131700` → missing .NET runtime) — a classification is diagnostic
  knowledge, not a compatibility result.
- CI passing and the Python test suite being green — that proves the package
  works on the CI runner, never that Trados or memoQ runs.

What is required before any row below can be recorded (per
`SECURITY_REMEDIATION_STATUS.md` §4):

1. A legitimately licensed installer (never a cracked or shared copy).
2. A clean, disposable Wine prefix; record its architecture.
3. The documented diagnose run against the real log
   (`wine-ai diagnose --log ... --profile ...`).
4. A complete workflow attempt, with pass/fail recorded per step.

## Evidence record template (one block per tested release)

Copy this block for each application release tested. Fill every field; write
`BLOCKED` where a step could not run, and keep sanitized evidence only
(no license keys, no customer documents, no raw confidential logs).

```markdown
### Run INC-record: <APP> <exact version/build> — <YYYY-MM-DD>

- Tester / operator: <name or handle>
- Installer provenance: <official download / physical media / vendor portal>
- License entitlement: <valid license held: yes/no>  (never paste keys)
- Host: <distribution + release, kernel, desktop session>
- GPU / driver: <vendor, model, driver version>
- Wine build: <version + build source>  |  Prefix arch: <win32/win64>
- Prefix identity: <non-sensitive identifier>, created fresh: <yes/no>
- Recipe revision used: <commit or revision id>

| # | Workflow step (per TEST_MATRIX) | Result | Evidence reference |
|---|---|---|---|
| 1 | Installer launch | UNPROVEN / PASS / FAIL / BLOCKED | <sanitized ref> |
| 2 | Installer completion | | |
| 3 | First launch | | |
| 4 | License activation | | |
| 5 | Document open | | |
| 6 | Document edit / save / reopen | | |
| 7 | Fonts and UI rendering | | |
| 8 | Export / print | | |
| 9 | Terminology / TM workflow (if used) | | |
| 10 | Restart and repeat launch | | |
| 11 | Clean shutdown | | |

- Diagnose output summary: <what wine-ai reported, sanitized>
- Known failures / limitations observed:
- Repeated from a clean state: <yes/no>
- Verdict for THIS scoped configuration: <UNPROVEN / PARTIALLY_PROVEN / PROVEN /
  CONTRADICTED> — scope: <exact app release + config + workflows tested>
```

## Rules for updating the verdict table

1. Never generalize a scoped result to "Trados works" or "memoQ works".
2. A `PARTIALLY_PROVEN` row must name exactly which workflows passed and which
   remain untested.
3. `CONTRADICTED` requires reproducible evidence and is recorded even when the
   result is unwelcome.
4. Update `docs/cat-tools/TEST_MATRIX.md` "Current status" only together with a
   filled evidence block above.
5. Nothing in this file authorizes re-enabling Bottle creation or any write
   path; discovery and diagnostics remain read-only.
