@AGENTS.md

## Multi-session working agreement (Claude Code hooks)

The rules are in AGENTS.md § Multi-session working agreement; `.claude/settings.json` enforces them here. The SessionStart hook prints the shared-checkout state—read it before touching anything. The PreToolUse hook denies destructive git commands; if it blocks you, coordinate rather than work around it. It matches the whole Bash command string, so keep those literal git incantations out of commit messages and heredocs.
