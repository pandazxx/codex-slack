---
description: Create and push a git tag; proposes the next version if no name is given
argumentHint: "[tag name, e.g. v1.2.3-rc1 or staging-fix-cache]"
model: claude-haiku-4-5-20251001
---

Create and push a git tag on the current commit.

## Supported tag formats

| Format | Example | Triggers |
|---|---|---|
| `v<version>-rc<N>` | `v4.3-rc9` | `build-rc.yml` — builds all three images, pushes `:rc` |
| `staging-<desc>` | `staging-fix-cache` | `build-staging.yml` — builds all three images, pushes `:staging` |

For `staging-<desc>` tags, `<desc>` must be **≤ 20 characters** (kebab-case, no spaces).

## Procedure

If the user provided a tag name in the arguments, validate it and skip to the creation step.

If no tag name was given:
1. Run `git tag --sort=-version:refname | head -10` to see recent tags.
2. Ask the user which type of tag they want:
   - **RC tag** — infer the next RC (e.g. if latest is `v4.3-rc9`, propose `v4.3-rc10`).
   - **Staging tag** — ask for a short description (≤ 20 chars), then propose `staging-<desc>`.
3. State the proposed tag and ask for confirmation before creating it.

## Validation

Before creating the tag:
- For `staging-<desc>`: verify `<desc>` is ≤ 20 characters. If longer, reject and ask for a shorter description.
- For `v*-rc*`: verify the format matches `v<major>.<minor>-rc<N>`.
- Any other format: confirm explicitly with the user that the format is intentional.

## Creation

Once the tag name is confirmed:
1. Run `git tag <tag-name>` on HEAD.
2. Run `git push origin <tag-name>`.
3. Confirm the tag was pushed and report the tagged commit SHA.
