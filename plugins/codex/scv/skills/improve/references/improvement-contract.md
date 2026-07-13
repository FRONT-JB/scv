# SCV improvement contract

## Invariants

1. Support macOS only.
2. Keep learning state under `<git-common-dir>/scv/learning`.
3. Let only the deterministic controller write observations and candidates.
4. Run the Failure Analyst as an ephemeral, read-only Codex process.
5. Keep failure evidence and analyst evidence in separately hashed directories.
6. Redact secrets before analyst prompts or persistent learning records.
7. Analyze one step/run/signature at most once and keep worker attempts capped at three.
8. Treat analyst failure as a degraded learning feature, not a new worker failure.
9. Inject only `active` lessons, at most three, for an exact failure signature.
10. Never let a lesson change approved scope, acceptance criteria, or controller policy.
11. Never modify, install, merge, push, or publish the running SCV automatically.
12. Repair SCV only in an explicitly approved source checkout through a separate full workflow.
13. Keep timeouts in the original task; do not turn them into cross-task lessons or SCV repair proposals without separate verified controller-defect evidence.

## Lifecycle

```text
observation
  └─ successful verified retry → candidate
       ├─ final evidence + explicit approval → active
       └─ explicit retirement → retired

active
  ├─ same failure recurs → suspect
  └─ explicit retirement → retired

suspect
  └─ explicit retirement → retired
```

Candidate, suspect, and retired lessons must never be injected into a worker.
A suspect lesson cannot be reactivated; a later verified repair creates a new
observation and candidate that follows the normal approval path.

## Improvement proposal boundary

A verified `controller-defect` proposal is evidence for a new task, not
authorization to patch SCV. Ordinary implementation exhaustion, final
acceptance failure, timeout, and environment failure stay with the original
task and must not be mislabeled as SCV source defects. The new repair task must
target the scv source checkout, start from intake, prove a
red-before/green-after regression, use a separate worktree, run the complete
SCV suite, and stop at handoff for human review.

Proposal lifecycle is `proposed → handed-off → closed`, or directly `proposed →
closed` when explicitly dismissed. Handoff must record the approved source repo
and full repair task ID. The controller verifies a tracked
`plugins/codex/scv` source tree, rejects the installed Codex plugin area, and
requires a task ID different from the originating task before recording the
handoff. Closure must record a reason. Neither transition grants merge,
installation, publication, or cleanup authority.
