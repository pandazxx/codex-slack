# FAQ

Frequently asked questions and answers about operating this project.

---

**Q: What is the difference between bot mode and master mode?**

Bot mode (`python -m src.bot.main`) runs a single Slack bot that attaches to one local Codex session. Master mode (`python -m src.master.main`) runs an orchestration control plane that spins up and manages multiple agent containers, each targeting a different repository and mapped to a different Slack or Discord channel.

---

**Q: Do I need a public webhook URL?**

No. Both bot mode and master mode use Slack Socket Mode, which establishes an outbound WebSocket connection. No inbound firewall rules or public URLs are required.

---

**Q: Can I use Claude Code instead of Codex?**

Yes. When loading an agent in master mode, pass `--adapter claude-code` to `/master-agent-load`. Set `MASTER_CLAUDE_COMMAND_TEMPLATE` to override the default command. Ensure the `claude` CLI is present in the agent container image (`Dockerfile.agent-minimal` or a custom image built from this branch).

---

**Q: How do I change the model used by an agent?**

Use `/master-agent-set-model <name> [model]` in the admin channel. Omit the model argument to clear the override and fall back to the default. Alternatively, set `MASTER_CLAUDE_CONFIG_DIR_PATH` to mount a `~/.claude/settings.json` into all agents; changes take effect on the next prompt dispatch without restart.

---

**Q: The agent responds with `sh: 1: claude: not found`.**

The container image does not include the `claude` CLI. Rebuild the base image from the current branch, which includes the Claude Code adapter, and set `MASTER_AGENT_BASE_IMAGE` to the new tag.

---

**Q: Where do I find the agent registry?**

Default path: `data/master/agents.json`. Override with `MASTER_REGISTRY_PATH`.

---

**Q: Does the agent support complex Word documents (tracked changes, macros, embedded OLE objects, rich formatting)?**

Not fully in v3.5. The inbound conversion is best-effort — plain text and inline images are extracted, but tracked changes, macros, and embedded OLE objects are not preserved. For best results in v3.5, use `.md` or `.txt` + separate image attachments. Full complex docx fidelity is in the backlog for a future version.
