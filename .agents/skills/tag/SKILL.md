---
name: tag
description: Use when creating and pushing a git tag for the current commit, including proposing the next version when the tag name is not provided.
---

# Tag

Use this skill for release or release-candidate tagging.

## Steps

1. If the user supplied a tag name, use it directly.
2. Otherwise inspect tags matching `v*` and start from the largest existing version.
3. Follow the repository release-candidate pattern `v1.2-rc3`.
4. If no RC exists for a version yet, start at `-rc1`.
5. If RC tags already exist for the chosen version, increment the RC number by one.
6. Create the tag on the current commit.
7. Push the tag.
8. Report the tag name and commit SHA.

## Constraints

- Unless the user explicitly asks for a different target version, always derive the next tag from the largest existing `v*` tag.
