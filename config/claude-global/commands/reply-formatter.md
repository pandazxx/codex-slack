Reformat the draft text provided in the arguments so it is ready to send on the current platform.

$ARGUMENTS

Read the `AGENT_FRONTEND` environment variable and apply the matching rules:

**If `AGENT_FRONTEND=discord`:**
- Bold: use `**double asterisks**`, not `*single*`
- Headers (`## Heading`) → bold label (`**Heading**`)
- Horizontal rules (`---`) → remove entirely
- Markdown pipe tables → convert to a fixed-width monospace code block (align columns)
- Architecture or flow diagrams → use a `\`\`\`mermaid` block; they are rendered as images
- No HTML tags

**If `AGENT_FRONTEND=slack` or unset:**
- Bold: use `*single asterisks*`, not `**double**`
- Mermaid blocks → replace with an ASCII diagram or a plain prose description
- Pipe tables → keep as-is (Slack renders them)
- No HTML tags

In both cases:
- Bullet lists use `-`
- Inline code uses single backticks
- Do not add `#`/`##` headers — use bold labels instead
- Prefix the reply with `<AI_AGENT_NAME> says:` (using the env var; fall back to `agent` if unset)

Output only the reformatted reply, ready to send. Do not add any explanation or preamble.
