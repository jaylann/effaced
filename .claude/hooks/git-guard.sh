#!/usr/bin/env bash
# PreToolUse(Bash) guard: block destructive git commands that discard
# uncommitted work. Escape hatch: append `# yes-destroy` when the user
# explicitly asked for the discard in their current message.
set -euo pipefail

input=$(cat)
command=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[ -z "$command" ] && exit 0

# explicit user-sanctioned escape hatch
case "$command" in
  *"# yes-destroy"*) exit 0 ;;
esac

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

if printf '%s' "$command" | grep -qE 'git[[:space:]]+reset[[:space:]]+(--hard|--merge)'; then
  deny "git reset --hard discards uncommitted work. Use git stash to park changes, or append '# yes-destroy' if the user explicitly asked for the discard."
fi
if printf '%s' "$command" | grep -qE 'git[[:space:]]+clean[[:space:]]+-[a-zA-Z]*f'; then
  deny "git clean -f deletes untracked files. Append '# yes-destroy' only if the user explicitly asked."
fi
if printf '%s' "$command" | grep -qE 'git[[:space:]]+checkout[[:space:]]+(--[[:space:]]+\.|\.([[:space:]]|$))'; then
  deny "git checkout -- . discards uncommitted work. Use git stash, or '# yes-destroy' if explicitly requested."
fi
if printf '%s' "$command" | grep -qE 'git[[:space:]]+restore[[:space:]]' && \
   ! printf '%s' "$command" | grep -qE 'git[[:space:]]+restore[[:space:]]+--staged'; then
  deny "git restore <path> discards uncommitted work. Use git stash or git show <ref>:<file>, or '# yes-destroy' if explicitly requested."
fi

exit 0
