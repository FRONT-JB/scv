# SCV workflow contract

Use this contract as the authoritative model for task state, gates, and artifacts. The control-plane script owns persisted state; never edit its files manually.

## Invariants

1. Persist mutable task control data, approvals, locks, and execution evidence under the repository's Git common directory. Track only the immutable approved specification, plan, and manifest copies under `.scv/tasks/<task-id>/` in the sealed plan commit.
2. Create no branch or worktree before both the specification and plan are explicitly approved.
3. Bind each task to two distinct revisions: the approved source base `A` used by the plan and implementation diff, and the sealed plan commit `P` used as the frozen execution `HEAD`. Revalidate the base and approved artifacts immediately before materialization.
4. Run implementation only in the recorded worktree.
5. Advance from worker completion only after acceptance commands are independently recorded as passing.
6. Re-run every step acceptance command and a whole-plan read-only verifier before execution becomes ready.
7. Fingerprint approved artifacts plus the verified worktree HEAD and content; require `P` at `HEAD`, keep the tracked `.scv` snapshot unchanged, and block if any identity changes before its next gate.
8. Preserve task identity across stops, blocks, and target promotion.
9. Treat `READY` as logical completion. Never infer worktree removal, merge, push, or publication from it.
10. Keep control-plane transitions deterministic and conversational design decisions visible to the user.
11. Serialize every task mutation with a process lock and commit multi-field recovery decisions in one state revision.
12. Run acceptance commands with network disabled, a controller-owned environment allowlist, reads denied for enumerated host credential paths, and filesystem writes limited to the recorded worktree plus a controller-owned `0700` per-command scratch directory in the macOS system temporary area, outside every worktree and the Git common directory.
13. Start nested Codex sessions with an isolated temporary `CODEX_HOME`; carry authentication only, not user instructions, skills, plugins, rules, or configuration. Give the model shell a separate temporary HOME and deny reads to the linked and resolved source authentication paths plus enumerated SSH, cloud, package-manager, and keychain credential paths.
14. Hash every passed step and final evidence directory. Recheck path containment, symlink absence, file presence, and the hash before trusting persisted success or reporting ready status.
15. Hold a non-blocking task execution lease in the control plane and a run-directory lock in the executor so concurrent invocations cannot share an evidence path or overwrite an index revision.
16. Run only on macOS and reject unsupported hosts before creating or mutating task or run state.
17. Freeze actionable failure evidence before launching a read-only Failure Analyst, and store analyst evidence under a separately hashed path.
18. Keep cross-task learning human-gated: create candidates after a verified retry, inject only active exact-signature lessons, and route SCV defects to proposal-only improvement work.
19. Add the mapped SCV voice line only at CLI output boundaries. Never persist `scv_line`, use it for state transitions, or treat it as stronger evidence than `state`, `status`, exit codes, and verified artifacts.
20. Keep plan v2 retries shallow: use an approved attempt budget, compare only controller-owned failure and workspace fingerprints, and stop repeated or oscillating failures without another model call or acceptance run.
21. When the repository root declares an exact `packageManager` pin, resolve an already-installed same-name binary from the controller PATH, verify its exact version once in the network-disabled external scratch before any worker runs, and expose only that verified binary through a per-command wrapper. Never auto-download or bootstrap a package manager during acceptance.
22. Bound each subprocess's combined stdout/stderr capture to 8 MiB. Treat overflow as an infrastructure blocker and terminate the controller-owned process group, just as for timeout or cancellation. After the command leader exits, terminate any background descendants still in that group before returning.
23. Create `P` with a temporary Git index and Git plumbing, then atomically publish the task branch before adding its linked worktree. Never switch or modify the invoking worktree to create the plan commit.
24. Persist the complete materialization intent before publishing the branch, and recover an interruption only after the branch parent, tree, approved hashes, worktree identity, and cleanliness match that intent.
25. Treat approved specification and plan contents as Git-publishable data before materialization. Never place credentials, tokens, raw secrets, or private source material that must not persist in Git objects into either artifact.

## Targets

| Target | Required terminal gate | Worktree |
| --- | --- | --- |
| `analyze` | Approved specification | Never created |
| `plan` | Approved implementation plan | Never created |
| `full` | Verified execution and handoff | Created after plan approval |

An `analyze` task may be resumed into `plan`, and a `plan` task may be resumed into `full`. Promotion keeps the task ID and approved artifacts. A completed `full` task does not promote further.

## States and transitions

| Current state | Command or event | Next state |
| --- | --- | --- |
| none | `start TARGET ...` | `INTAKING` |
| `INTAKING` | `submit-spec TASK_ID --spec FILE` | `AWAITING_SPEC_APPROVAL` |
| `AWAITING_SPEC_APPROVAL` | `approve-spec TASK_ID` for `analyze` | `READY` |
| `AWAITING_SPEC_APPROVAL` | `approve-spec TASK_ID` for `plan` or `full` | `PLANNING` |
| `PLANNING` | `submit-plan TASK_ID --plan FILE` | `AWAITING_PLAN_APPROVAL` |
| `AWAITING_PLAN_APPROVAL` | `approve-plan TASK_ID` for `plan` | `READY` |
| `AWAITING_PLAN_APPROVAL` | `approve-plan TASK_ID` for `full` | `BASE_REVALIDATION` |
| `BASE_REVALIDATION` | successful `materialize TASK_ID` | `EXECUTING` |
| `EXECUTING` | successful `execute TASK_ID` | `HANDOFF` |
| `HANDOFF` | successful `handoff TASK_ID` | `READY` |
| active state | recoverable interruption | `BLOCKED` with a saved continuation state |
| `BLOCKED` | `resume TASK_ID` after correction | saved continuation state |
| `READY` for `analyze` | `resume TASK_ID` | `PLANNING` with target `plan` |
| `READY` for `plan` | `resume TASK_ID` | `BASE_REVALIDATION` with target `full` |
| nonterminal state | confirmed `abandon TASK_ID` | `ABANDONED` |

Reject out-of-order commands. Approval transitions require an already submitted artifact and an explicit user approval to the exact artifact revision. Replacing an artifact invalidates its previous approval.

Public control-plane JSON for a task includes computed `state_label` and
`scv_line` fields. Human-facing progress reports render them as
`<state_label> — "<scv_line>"` and do not expose the raw English state code as a
heading. Machines must continue to branch on the lifecycle `state` and exit code.
Executor status output applies the same presentation rule to `pending`,
`running`, `ready`, and failure statuses.

While a full task is `EXECUTING`, `status TASK_ID` also returns a sanitized
`execution_progress` object. It contains only `status`, `stage`, the computed
Korean `stage_label`, completed and total step counts, the current step
ID/position/status when applicable, the bounded attempt number, a
controller-authored message, `updated_at`, and the computed stage `scv_line`. A terminal snapshot may also contain a bounded
`termination` object with its code, next action, step ID, and attempt. It never returns plan instructions, prompts, raw
stdout/stderr, acceptance output, findings, evidence bodies, or environment
values. The snapshot is read from an atomically replaced v1 index without taking
the executor's exclusive run lock, and its task, plan, source base, execution head, and workspace
bindings must match the durable task before it is returned.

Public execution stages are `starting`, `worker`, `acceptance`, `verifier`,
`failure-analysis`, `retry`, `step-complete`, `final-acceptance`,
`final-verifier`, `complete`, `blocked`, `failed`, and `cancelled`. Consumers
must treat `stage` as progress presentation and the top-level lifecycle `state`
and command exit code as authoritative control signals.

## Control-plane commands

All commands use this prefix:

```text
python3 "<plugin-root>/scripts/scv.py" --repo "<repo>"
```

| Command | Purpose |
| --- | --- |
| `start TARGET --task-id ID --request TEXT [--base BRANCH]` | Create task state and capture the base revision |
| `status TASK_ID` | Return authoritative state, artifacts, worktree, and blockers |
| `submit-spec TASK_ID --spec FILE` | Copy and fingerprint the proposed specification |
| `approve-spec TASK_ID` | Record explicit approval of the current specification |
| `submit-plan TASK_ID --plan FILE` | Copy and fingerprint the proposed implementation plan |
| `approve-plan TASK_ID` | Record explicit approval of the current plan |
| `materialize TASK_ID [--worktree PATH] [--branch NAME] [--adopt-existing]` | Revalidate `A`, seal approved documents in `P`, and create, recover, or explicitly adopt the isolated worktree |
| `execute TASK_ID [--timeout SECONDS]` | Invoke the executor against the approved plan |
| `handoff TASK_ID` | Collect final diff and verification evidence and mark ready |
| `resume TASK_ID` | Recover a block or promote the same task to its next target |
| `abandon TASK_ID` | Record explicit abandonment without implicit destructive cleanup |

Use only options reported by each script's `--help`. Pass paths as separate arguments and never interpolate task content into shell syntax.

## Specification artifact

The specification must be self-contained and include:

- original request and user-visible outcome;
- current behavior and evidence inspected;
- goals and non-goals;
- functional and quality requirements;
- repository, product, security, and compatibility constraints;
- observable acceptance criteria;
- risks, assumptions, and unresolved decisions.

Do not include an implementation plan disguised as requirements. Cite file or symbol evidence only when it was actually inspected.

## Plan artifact

The plan must trace back to the approved specification and include:

- captured base branch and revision;
- ordered steps with stable IDs; array order is the dependency order;
- intended files or symbols and the reason for each change;
- test-first or characterization strategy appropriate to the repository;
- exact acceptance commands and expected evidence;
- migration, compatibility, documentation, and rollback considerations;
- final handoff checks and known residual risk.

Each step must be independently understandable by an execution worker. Avoid vague steps such as "update tests" or "fix related code".

New plans must use this exact JSON v2 shape. The control plane injects and fingerprints `expected_base_sha` (`A`) when it stores the approved candidate, so omit that field from the draft. The later plan commit SHA (`P`) cannot self-reference from inside `plan.json`; the control plane records it as `plan_anchor.commit_sha` and the executor records it as `expected_head_sha`. The executor continues to read v1 plans with their legacy three-attempt behavior, but the skill must not create new v1 plans.

```json
{
  "schema_version": 2,
  "task_id": "20260713-short-slug",
  "task": "Concise implementation outcome",
  "loop_policy": {
    "max_attempts": 2,
    "detect_stagnation": true
  },
  "steps": [
    {
      "id": "step-1",
      "title": "Observable step title",
      "instructions": "Concrete files, symbols, behavior, tests, and boundaries for this step.",
      "acceptance": [
        "exact controller-owned verification command"
      ],
      "timeout_seconds": 1800
    }
  ],
  "final_acceptance": [
    "exact whole-task verification command"
  ]
}
```

`timeout_seconds` and `final_acceptance` are optional. `loop_policy.max_attempts` is the total attempt count and must be from 1 through 3. `detect_stagnation` must be a boolean; keep it `true` for the shallow profile. Every step needs at least one non-empty acceptance command. Do not put `git push` in any acceptance command. The ordered steps, not an undeclared dependency field, define execution order.

The submit command uses the executor's exact validator before changing task state. Unknown keys, boolean or out-of-range timeouts, mismatched task IDs or base revisions, and unsafe publication commands are rejected before plan approval or worktree creation.

Acceptance and final-acceptance commands run through `codex sandbox` with a controller-generated permission profile. The profile grants read access needed to run local tools, grants write access only to the recorded worktree and a controller-owned per-command scratch directory, denies the source Codex home and enumerated SSH, cloud, package-manager, Git credential, and keychain paths, and disables network and arbitrary Unix-socket access. Create that `0700` scratch under `/private/tmp`, independently of host `TMPDIR`, and reject it if it overlaps any linked worktree or the Git common directory. Build the acceptance parent environment from a small allowlist so API keys, tokens, secrets, passwords, database URLs, askpass helpers, and cloud credentials are not inherited. A missing or unsupported sandbox is a blocker; the controller must never fall back to an unsandboxed shell. This is an exact sensitive-path boundary rather than a global user-home read denial, because macOS developer tools such as NVM-installed Node and pnpm may require read traversal under the user home.

If the root `package.json` has an exact `npm`, `pnpm`, `yarn`, or `bun` `packageManager` pin, acceptance has an additional preflight. Resolve the named executable from the safe controller PATH, disable Corepack network/project switching and package-manager auto-version management, and run exactly one `--version` probe in the network-disabled external scratch with a 15-second limit. Continue only when the reported semantic version exactly matches the pin, then prepend a scratch-local wrapper that executes the verified absolute binary. A missing binary, malformed or unsupported pin, mismatch, probe timeout, or bootstrap/network failure is an infrastructure blocker before worker dispatch and consumes no implementation attempt. Disabling auto-version management alone never authorizes a mismatched global binary. Repositories without a `packageManager` field keep the existing behavior.

## Failure behavior

- Preserve full command, exit status, and stderr for failed acceptance checks.
- Allow only the attempt count approved in `loop_policy`, with a hard cap of three and a recommended shallow value of two. Budget exhaustion resumes at `PLANNING`, requires a materially revised and re-approved plan, and starts a new `runs/<plan-sha>/` evidence set without deleting the old one.
- After each failed v2 attempt, derive a convergence fingerprint from the normalized failure signature, acceptance result vector, and authoritative workspace fingerprint. Stop with `stalled` for an identical consecutive fingerprint, `oscillating` when an older fingerprint recurs, and `verifier_disagreement` when the same verifier failure repeats without a workspace change. Fingerprinting must not invoke a model or rerun acceptance.
- Persist a named termination and bounded next action. `budget_exhausted`, `stalled`, `oscillating`, and `verifier_disagreement` require a revised and re-approved plan before execution resumes.
- Classify missing tools, invalid commands (including shell exit 126/127), stale base revisions, and state mismatches as blockers rather than implementation failures.
- Do not consume a step attempt when Codex, the sandbox, or a required executable cannot be launched. Preserve the blocker and retry only after the environment is corrected.
- When a restart finds a durable `running` attempt that the prior controller did not finish, move it to the controller blocker audit list and reuse the same implementation attempt number. A controller crash must not consume the approved worker budget.
- Never silently change the approved plan to make execution pass.
- Require a revised plan and new approval when scope, base assumptions, or acceptance criteria materially change.
- If the verified worktree changes before handoff, return to `EXECUTING` and require full revalidation before handoff can continue.
- Keep worktree and task evidence available after failure and after `READY`.
- Analyze only actionable worker, acceptance, and verifier failures. Do not analyze cancellation, base drift, infrastructure blockers, or analyst failures.
- Invoke at most one Failure Analyst for the same step, run, and failure signature. An analyst failure must preserve the original retry behavior.
- Store a repaired failure as a candidate lesson only. Require final execution evidence and explicit approval through `$scv:improve` before activation.
- Mark an active lesson `suspect` and stop injecting it when the same failure signature recurs after application.
- Create a SCV improvement proposal only when the independent analyst classifies a verified failure as a controller defect. Ordinary retry exhaustion, final-validation failure, timeout, and environment failure stay in the original task and never authorize SCV source repair.

## Worktree ownership and recovery

Before creating a worktree, persist its intended absolute path, branch, source base `A`, plan tree, approved artifact hashes, and tracked `.scv` root. Build the plan tree from `A` with a temporary index, create a single-parent conventional `docs(scv)` commit `P`, and publish the new task ref with an atomic create-only update. Only then create the linked worktree with its branch at `P`; do not check that branch out in the invoking worktree.

An existing worktree may be adopted only when either a previous SCV creation intent proves crash recovery or the user explicitly selected `--adopt-existing`. In both cases, require the exact task branch at `P`, the expected plan tree and parent `A`, byte-identical approved documents, and a clean worktree. Reject a branch still at `A`, unrelated, dirty, or merely name-matching worktrees. If interruption occurs after intent or ref publication, validate and reuse the recorded objects rather than creating a competing history.

## Runtime boundary

The supported local runtime is macOS with Git, Python 3.9 or newer, Codex CLI 0.144.1 or newer, POSIX `sh`, and `fcntl`. Linux, WSL, and Windows are outside the contract. Reject an unsupported host before task or run state is created or mutated. A `full` preflight must also verify the required `codex exec` and `codex sandbox` flags and start the fail-closed macOS acceptance sandbox. In a managed Codex session, invoke `start full`, `plan`-to-`full` promotion, `materialize`, and `execute` with host execution approval so the controller can own the nested Seatbelt boundary. This approval lifts only the outer controller invocation; it must not disable or widen the controller's inner worker, analyst, verifier, or acceptance sandboxes.
