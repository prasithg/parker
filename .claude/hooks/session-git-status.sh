#!/bin/bash
# SessionStart hook: every new session in this shared checkout sees the
# current git state before doing anything. Multiple Claude Code sessions
# (plus a Hermes agent) may be working here simultaneously.

timeout 10 git fetch origin --quiet 2>/dev/null || true

branch=$(git branch --show-current 2>/dev/null || echo "?")
dirty=$(git status --porcelain 2>/dev/null | grep -cv '^?? \.DS_Store' | tr -d ' ')

echo "[shared-checkout] branch: ${branch}; uncommitted changes: ${dirty}"
if [ "${dirty}" != "0" ]; then
  echo "[shared-checkout] dirty paths (may belong to ANOTHER live session — do not stash/reset/discard; coordinate via branches):"
  git status --short | grep -v '^?? \.DS_Store' | head -12
fi
echo "[shared-checkout] recent branches:"
git for-each-ref --sort=-committerdate --format='  %(refname:short)  (%(committerdate:relative))' refs/heads 2>/dev/null | head -5
echo "[shared-checkout] rules: work on a feature branch, never long work directly on main; if the tree has changes you did not make, STOP and ask the user. See CLAUDE.md 'Multi-session working agreement'."

exit 0
