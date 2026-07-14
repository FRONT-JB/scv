---
name: workflow
description: Run a single resumable macOS software-change workflow from requirements intake through specification approval, implementation planning, plan approval, isolated execution, failure-aware retry, verification, and handoff. Use when Codex is asked to analyze, plan, implement, continue, or report on a repository change with SCV; when a task must stop after analysis or planning; or when an existing SCV task ID must be resumed without repeating completed stages.
---

# SCV Workflow

## Overview

Treat SCV as one public process with three stopping targets: `analyze`, `plan`, and `full`. Keep conversational judgment in this skill and delegate state transitions, worktree creation, execution, and evidence collection to the bundled scripts.

Read [workflow-contract.md](references/workflow-contract.md) before starting or resuming a task. Follow its state transitions and artifact requirements exactly.

## Set Up the Invocation

1. Resolve the repository root with `git rev-parse --show-toplevel` unless the user supplied a repository.
2. Resolve `<plugin-root>` as two directories above this skill directory. Use `python3 "<plugin-root>/scripts/scv.py" --repo "<repo>" ...` for every control-plane command.
3. Choose the target from the request:
   - `analyze`: capture and approve the specification, then stop.
   - `plan`: capture and approve the specification and plan, then stop.
   - `full`: continue through worktree execution and handoff.
4. Reuse a user-supplied task ID. Otherwise create a stable ID in the form `YYYYMMDD-short-slug` and show it immediately.
5. Never edit SCV state files directly. Treat command exit codes and structured output as authoritative.
6. Run a read-only preflight before `start`: require macOS, confirm the repository and its instructions are readable, verify Git, POSIX `sh`, and Python 3.9+ are available, and—for `full`—verify Codex CLI 0.144.1+ provides both `codex exec --help` and `codex sandbox --help`. Stop before creating task state on any non-macOS host. A dirty checkout is not itself a reason to modify, stash, or discard user work. Report a missing prerequisite as a blocker.
7. Treat `start full`, a `resume` that promotes a completed `plan` task to `full`, `materialize`, and `execute` as host-owned controller commands. Before invoking one from a managed Codex session, request host execution approval through the surface's escalation mechanism. Do not grant the nested workers broader access: the controller still launches them with its isolated `CODEX_HOME` and its own least-privilege sandboxes. If macOS reports `sandbox_apply: Operation not permitted`, retry the same controller command with host approval; do not bypass the controller or disable its inner sandbox.

Start a new task:

```text
python3 "<plugin-root>/scripts/scv.py" --repo "<repo>" start <target> --task-id <task-id> --request <request> [--base <branch>]
```

For an existing task, run `status <task-id>` first. If it is paused, use `resume <task-id>` and continue from the returned state; do not replay completed stages. When `status` shows a `READY` `plan` target, run the promoting `resume` as a host-approved command because it performs the full-runtime Seatbelt preflight before changing the durable target.

Every control-plane result containing a lifecycle `state` also contains computed
`state_label` and `scv_line` fields. When reporting state or progress, show it
once as `<state_label> — "<scv_line>"`, followed by the Korean explanation and
next action. Never expose the raw English state code as the conversational
heading; it is the machine-readable control value only. Treat both presentation
fields as non-authoritative: state values, command exit codes, approvals, and
recorded evidence remain authoritative. Never infer approval or recovery from a
voice line.

## Run the Pipeline

### 1. Intake and specification

Inspect repository instructions, relevant code, tests, documentation, and recent history using read-only operations. Establish the requested outcome, current behavior, non-goals, constraints, acceptance evidence, risks, and unresolved decisions. Ask only questions whose answers materially change scope or design.

Write a self-contained specification artifact outside tracked repository content, then submit it:

```text
python3 "<plugin-root>/scripts/scv.py" --repo "<repo>" submit-spec <task-id> --spec <file>
```

Present the specification and request explicit approval. Approval must be a user response to the current artifact; do not infer it from silence or from an earlier request to proceed. After approval, run `approve-spec <task-id>`. For `analyze`, report the resulting `READY` state and stop without creating a branch or worktree.

### 2. Implementation plan

For `plan` and `full`, turn the approved specification into ordered, reviewable steps. Name concrete files or symbols where evidence supports them, encode dependencies by step order, include exact verification commands, and identify rollback and handoff evidence. Keep unknowns explicit instead of inventing repository facts. Use the exact plan v2 JSON shape in the workflow contract with the shallow loop policy; the executor rejects unknown fields. Legacy v1 plans remain readable only for backward compatibility.

Write the plan artifact outside tracked repository content, then submit it:

```text
python3 "<plugin-root>/scripts/scv.py" --repo "<repo>" submit-plan <task-id> --plan <file>
```

Present the plan and request a separate explicit approval. Run `approve-plan <task-id>` only after that response. For `plan`, report the resulting `READY` state and stop without creating a branch or worktree.

### 3. Revalidate and materialize

For `full`, run `status <task-id>` and confirm the approved base revision and plan are still valid. Then run the following as a host-approved controller command:

```text
python3 "<plugin-root>/scripts/scv.py" --repo "<repo>" materialize <task-id> [--worktree <path>] [--branch <name>] [--adopt-existing]
```

Do not create a branch or worktree by any other route. Pass `--adopt-existing` only when the user explicitly chose an already-created worktree; the controller still requires an exact branch, approved base `HEAD`, and a clean checkout. If base revalidation blocks the task, explain the delta, return to planning with `resume`, revise and resubmit the plan, and obtain plan approval again. Never bypass the gate.

### 4. Execute

Run the approved plan only inside the materialized worktree. Invoke the following as a host-approved controller command:

```text
python3 "<plugin-root>/scripts/scv.py" --repo "<repo>" execute <task-id> [--timeout <seconds>]
```

Start `execute` as one long-running host command and keep the returned exec/session
handle until it exits. While it is running, invoke the read-only `status <task-id>`
command separately every 15–30 seconds. `status` reads an atomically published,
sanitized snapshot and does not contend with the executor's run lock. Report only
changed `execution_progress` values as
`<state_label> — "<execution_progress.scv_line>" — 단계 X/Y, <execution_progress.stage_label>, N차 시도.`
Keep polling the original process handle as well; never launch a second `execute`
to obtain progress. The public snapshot intentionally omits prompts, raw command
output, evidence contents, and secrets.

When a terminal snapshot includes `termination`, report its code and
`next_action` without expanding the hidden reason or evidence. For
`budget_exhausted`, `stalled`, `oscillating`, or `verifier_disagreement`, return
to planning and require a materially changed, separately approved plan; do not
resume the same plan merely because unused attempts remain.

The control plane invokes `scripts/execute.py`; do not call the executor directly during the normal workflow. Direct executor use is reserved for recovery or debugging: inspect its `--help`, preserve the task state, and explain why bypassing the control-plane wrapper is necessary before doing so.

Monitor the command, preserve failure evidence, and report a blocked state promptly. Do not mark a step complete based only on worker narration; require the recorded acceptance checks. Use `resume <task-id>` after the blocking condition is corrected.

On an actionable worker, acceptance, or verifier failure, let the controller run its Failure Analyst flow. Do not reproduce it in the main session. The controller freezes the failed evidence, launches one ephemeral read-only analyst per step/run/signature, and injects the bounded diagnosis into the next worker. Plan v2 uses two total attempts by default and never permits more than three. When the same failure, acceptance vector, and workspace fingerprint repeat, or a prior fingerprint recurs, the controller stops early with a named termination instead of spending the remaining budget. This comparison uses persisted controller evidence and must not launch another model or validation command. Analyst failure degrades to the original retry behavior and must not create a new blocker.

Timeouts keep their bounded attempt evidence but do not produce cross-task
lessons or SCV repair proposals; treat them as an original-task execution or
environment condition unless separate evidence proves a controller defect.

### 5. Handoff

After successful execution, run:

```text
python3 "<plugin-root>/scripts/scv.py" --repo "<repo>" handoff <task-id>
```

Report the task ID, target, final state, branch and worktree, changed scope, verification results, unresolved risks, and recommended next action. `READY` is a logical completion state: do not delete the worktree, merge, push, or publish anything automatically. Cleanup or publication requires a separate explicit user instruction.

After execution succeeds or exhausts its attempts, inspect the learning queue:

```text
python3 "<plugin-root>/scripts/improve.py" --repo "<repo>" list --task-id <task-id>
```

Report candidate lessons and improvement proposals in Korean. Do not activate a candidate in this workflow. When the user wants to review, approve, retire, or hand off one of those artifacts, switch to `$scv:improve`; that flow revalidates the final evidence and keeps SCV source repair in a separate approved worktree.

## Resume and Abandon

- Run `status <task-id>` before any recovery action.
- Run `resume <task-id>` to recover `BLOCKED` from its saved continuation point without skipping approval gates.
- Reuse the same task ID to promote an `analyze` result into planning or a `plan` result into full execution. Do not create a duplicate task.
- Run `abandon <task-id>` only after explicit confirmation. Abandoning records the decision; it does not imply destructive worktree cleanup.
- If state and repository reality disagree, stop and report both. Never repair state by hand.
