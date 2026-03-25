---
name: tag
description: Use when creating and pushing a git tag for the current commit, including proposing the next version when the tag name is not provided.
---

# Tag

Use this skill for release or release-candidate tagging.

## Steps

1. Inspect recent tags if no tag name was supplied.
2. Propose the next logical version.
3. Create the tag on the current commit.
4. Push the tag.
5. Report the tag name and commit SHA.

## Constraints

- Do not guess a version if the repository tag pattern is unclear; ask instead.
