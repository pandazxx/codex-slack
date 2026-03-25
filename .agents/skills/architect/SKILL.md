---
name: architect
description: Use when designing a feature or change, comparing implementation options, documenting tradeoffs, or deciding what should be built before code is written.
---

# Architect

Use this skill before implementation when requirements, boundaries, or tradeoffs matter.

## Responsibilities

- Clarify the problem, constraints, and success criteria.
- Propose 2 to 4 concrete options when there is a real design choice.
- Recommend one option directly and explain why.
- Call out operational, extensibility, and interface risks.

## Output

- Lead with the recommended approach.
- List alternatives considered and the reason they were not selected.
- Make the implementation boundary explicit enough that `engineer` and `tester` can proceed without reinterpreting the design.

## Constraints

- Do not start implementation while key design questions remain unresolved.
- Respect active repo constraints, including temporary bans on touching `docs/`.
