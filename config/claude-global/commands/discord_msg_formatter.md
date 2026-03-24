---
description: Reformat a draft reply for Discord
argumentHint: "<draft text to reformat>"
---

Reformat the draft text provided in the arguments for Discord.

Rules:
- Bold: `**double asterisks**` — never `*single*`
- Bullet lists: `-`
- Inline code: single backticks
- No `#`/`##` headers — use bold labels instead
- No HTML tags, no horizontal rules
- Pipe tables: convert to a fixed-width monospace code block with aligned columns
- Architecture, flow, or sequence diagrams: use a ` ```mermaid ` block — Discord renders them as images

Output only the reformatted reply, ready to send — no explanation or preamble.
