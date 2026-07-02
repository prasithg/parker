#!/bin/bash
# PreToolUse guard for shared checkouts: multiple Claude Code sessions (and
# a Hermes agent) may work in this directory simultaneously. Git commands
# that discard or rewrite state could destroy ANOTHER session's uncommitted
# work, so they are denied here; a human can always run them manually.
# See CLAUDE.md "Multi-session working agreement".

cmd=$(jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$cmd" ] && exit 0

deny() {
  jq -n --arg r "Blocked in shared checkout: $1 Multiple sessions share this working directory and uncommitted changes may belong to another session (see CLAUDE.md 'Multi-session working agreement'). If this is genuinely needed, ask the user to run it manually." \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# Only inspect commands that actually invoke git.
echo "$cmd" | grep -qE '(^|[;&|[:space:]])git([[:space:]]|$)' || exit 0

if echo "$cmd" | grep -qE 'git[^|;&]*[[:space:]]reset[^|;&]*[[:space:]]--hard'; then
  deny "'git reset --hard' discards uncommitted work."
fi
if echo "$cmd" | grep -qE 'git[^|;&]*[[:space:]]stash([[:space:]]|$)' \
  && ! echo "$cmd" | grep -qE 'git[^|;&]*[[:space:]]stash[[:space:]]+(list|show)'; then
  deny "'git stash' hides another session's in-progress work."
fi
if echo "$cmd" | grep -qE 'git[^|;&]*[[:space:]]checkout[^|;&]*[[:space:]]--([[:space:]]|$)' \
  || echo "$cmd" | grep -qE 'git[^|;&]*[[:space:]]checkout[[:space:]]+\.([[:space:]]|$|;)'; then
  deny "'git checkout -- <path>' discards uncommitted work."
fi
if echo "$cmd" | grep -qE 'git[^|;&]*[[:space:]]restore([[:space:]]|$)' \
  && ! echo "$cmd" | grep -qE 'git[^|;&]*[[:space:]]restore[^|;&]*--staged' ; then
  deny "'git restore' discards uncommitted work."
fi
if echo "$cmd" | grep -qE 'git[^|;&]*[[:space:]]restore[^|;&]*--staged[^|;&]*--worktree'; then
  deny "'git restore --staged --worktree' discards uncommitted work."
fi
if echo "$cmd" | grep -qE 'git[^|;&]*[[:space:]]clean[^|;&]*[[:space:]]-[a-zA-Z]*[fd]'; then
  deny "'git clean -f/-d' deletes untracked files another session may have created."
fi
if echo "$cmd" | grep -qE 'git[^|;&]*[[:space:]]push[^|;&]*([[:space:]]--force([[:space:]]|$|-)|[[:space:]]-f([[:space:]]|$))'; then
  deny "force-pushing rewrites shared history."
fi
if echo "$cmd" | grep -qE 'rebase[^|;&]*--autostash|rebase\.autostash[[:space:]]*=?[[:space:]]*true|-c[[:space:]]+rebase\.autostash'; then
  deny "rebase with autostash silently stashes another session's uncommitted work."
fi
if echo "$cmd" | grep -qE 'git[^|;&]*[[:space:]]branch[^|;&]*[[:space:]]-D([[:space:]]|$)'; then
  deny "force-deleting a branch may destroy another session's unmerged work."
fi

exit 0
