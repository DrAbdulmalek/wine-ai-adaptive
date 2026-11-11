# Evidence policy for CAT application compatibility

## Core rule

Compatibility claims must be scoped to a specific application release, Wine build, Linux environment, prefix architecture, and workflow. Evidence for one configuration must not be generalized to all releases or systems.

## Evidence levels

- **PROVEN:** directly demonstrated by reproducible testing for the exact scoped claim.
- **PARTIALLY_PROVEN:** some behavior is demonstrated, but meaningful requirements remain untested.
- **UNPROVEN:** no sufficient direct evidence exists.
- **CONTRADICTED:** reliable evidence conflicts with the scoped claim.
- **BLOCKED:** the test could not be completed or the required evidence was unavailable.

## What does not prove compatibility

None of the following, alone, proves that Trados or memoQ works:
- A recipe file or dependency list.
- A successful CI run or unit test suite.
- A successful Wine version query or Vulkan device detection.
- An installer opening or completing.
- A single successful application launch.
- A synthetic log parser test.
- An external report without independently reproducible details.

## Required provenance

Every compatibility result should identify the application version/build, installer provenance, Wine version/build, Linux distribution/kernel, relevant driver stack, prefix architecture, test date, workflow tested, and sanitized supporting evidence. Record both successful and failed tests.

## Privacy and retention

Do not commit raw Wine logs, customer documents, translation memories, termbases, license keys, activation tokens, credentials, or private server details. Store only the minimum structured and sanitized evidence needed to support the claim. Do not submit confidential data to remote AI services.

## Recipe governance

The bundled Trados and memoQ recipes are hypotheses. Their `UNPROVEN` status must remain until evidence meets the scoped criteria in [TEST_MATRIX.md](TEST_MATRIX.md). A dependency suggestion must never be promoted to a guaranteed fix without a reproducible test.
