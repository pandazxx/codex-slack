---
description: Create and push a git tag; proposes the next version if no name is given
argumentHint: "[tag name, e.g. v1.2.3]"
model: claude-sonnet-4-6
---

Create and push a git tag on the current commit.

If the user provided a tag name in the arguments, use it directly and skip to the creation step.

If no tag name was given:
1. Run `git tag --sort=-version:refname | head -10` to see recent tags.
2. Infer the next logical version from the pattern (e.g. if the latest is `v3.3-rc3`, propose `v3.3-rc4`; if it is `v3.3.4`, propose `v3.3.5`).
3. State the proposed tag and ask for confirmation before creating it.

Once the tag name is confirmed:
1. Run `git tag <tag-name>` on HEAD.
2. Run `git push origin <tag-name>`.
3. Confirm the tag was pushed and report the tagged commit SHA.
