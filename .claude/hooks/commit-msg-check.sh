#!/usr/bin/env bash
# PreToolUse(Bash) guard: every commit must be Conventional Commits formatted
# and DCO signed-off (-s/--signoff or an explicit Signed-off-by trailer).
set -euo pipefail

input=$(cat)
command=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[ -z "$command" ] && exit 0

# only inspect actual git commit invocations
printf '%s' "$command" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+commit' || exit 0
# amend/fixup without a new message: nothing to validate here
printf '%s' "$command" | grep -qE '(--amend[[:space:]]+--no-edit|--fixup)' && exit 0

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

# DCO: require -s/--signoff flag or a Signed-off-by trailer in the message
if ! printf '%s' "$command" | grep -qE '(^|[[:space:]])(-s|--signoff)([[:space:]]|$)' && \
   ! printf '%s' "$command" | grep -q 'Signed-off-by:'; then
  deny "Commits must be DCO signed-off: add -s to git commit (Signed-off-by trailer). See CONTRIBUTING.md."
fi

# extract the first -m message (single- or double-quoted) for format checks
message=$(printf '%s' "$command" | sed -nE "s/.* -m[[:space:]]+'([^']+)'.*/\1/p; s/.* -m[[:space:]]+\"([^\"]+)\".*/\1/p" | head -1)
if [ -z "$message" ]; then
  # heredoc or editor-based message — let the server-side checks own it
  exit 0
fi

subject=$(printf '%s' "$message" | head -1)
if ! printf '%s' "$subject" | grep -qE '^(feat|fix|chore|docs|refactor|test|perf|style|ci|build|revert)(\([a-z0-9./-]+\))?!?: [a-z0-9]'; then
  deny "Commit subject must be Conventional Commits with lowercase subject: type(scope)?: subject — got: $subject"
fi
if [ "${#subject}" -gt 72 ]; then
  deny "Commit subject exceeds 72 characters (${#subject}). Shorten it."
fi

exit 0
