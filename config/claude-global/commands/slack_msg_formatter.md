---
description: Reformat a draft reply for Slack
argumentHint: "<draft text to reformat>"
---

Reformat the draft text provided in the arguments for Slack.

Rules:
- Bold: `*single asterisks*` — never `**double**`
- Bullet lists: `-`
- Inline code: single backticks
- No `#`/`##` headers — use bold labels instead
- No HTML tags, no horizontal rules
- No mermaid blocks — replace diagrams with ASCII art or plain prose
- Pipe tables: keep as-is (Slack renders them)

Output only the reformatted reply, ready to send — no explanation or preamble.
