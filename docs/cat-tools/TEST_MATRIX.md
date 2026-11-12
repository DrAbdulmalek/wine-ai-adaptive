# CAT application test matrix

This matrix defines what must be tested before changing a recipe's evidence status. It is not a report of successful tests.

## Current status

| Application | Recipe status | Last tested | Real-world compatibility established? |
|---|---|---|---|
| Trados Studio | UNPROVEN | null | No |
| memoQ Translator Pro | UNPROVEN | null | No |

## Required test dimensions

| Area | Trados Studio | memoQ |
|---|---|---|
| Exact application release/build | Required | Required |
| Vendor-supported host requirements reviewed | Required | Required |
| Wine version/build and distribution recorded | Required | Required |
| Prefix architecture and clean-state record | Required | Required |
| Installer launch and completion | Required | Required |
| First launch and repeat launch | Required | Required |
| License activation/deactivation | Required | Required |
| Core document open/edit/save/reopen | Required | Required |
| Fonts and UI rendering | Required | Required |
| Printing/export | Required | Required |
| Plugins/extensions, if used | Required | Required |
| Terminology/TM/project workflows | Required | Required |
| Network/server connectivity, if used | Required | Required |
| Upgrade/uninstall/recovery behavior | Required | Required |
| Reproducibility on a second clean test prefix | Strongly recommended | Strongly recommended |

## Evidence record

For each test, preserve:
- Date and tester.
- Application version/build and installer provenance.
- Wine version/build, distribution, kernel, graphics driver.
- Prefix architecture and a non-sensitive identifier.
- Exact steps and expected/actual outcomes.
- Relevant sanitized log signatures (not raw confidential logs).
- Whether the test was repeated from a clean state.
- Known failures, limitations, and environmental dependencies.

## Status transitions

- **UNPROVEN:** no sufficient real-world evidence.
- **PARTIALLY_PROVEN:** a narrow behavior was demonstrated, but important workflows or environments remain untested.
- **PROVEN:** only a clearly scoped claim has passed the required reproducible tests. Avoid saying simply “Trados works” or “memoQ works” without naming the tested release, configuration, and workflows.
- **CONTRADICTED:** reliable evidence disproves the specific scoped claim.

A successful unit test, synthetic log test, CI run, installer launch, or screenshot alone does not prove end-to-end application compatibility.
