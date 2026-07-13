# SCV workflow contract

Use this contract as the authoritative model for task state, gates, and artifacts. The control-plane script owns persisted state; never edit its files manually.

## Invariants

1. Persist task control data under the repository's Git common directory, not in tracked source content.
2. Create no branch or worktree before both the specification and plan are explicitly approved.
3. Bind execution to the approved base revision and plan. Revalidate both immediately before materialization.
4. Run implementation only in the recorded worktree.
5. Advance from worker completion only after acceptance commands are independently recorded as passing.
6. Re-run every step acceptance command and a whole-plan read-only verifier before execution becomes ready.
7. Fingerprint approved artifacts plus the verified worktree HEAD and content; require the approved base at HEAD and block if either identity changes before its next gate.
8. Preserve task identity across stops, blocks, and target promotion.
9. Treat `READY` as logical completion. Never infer worktree removal, merge, push, or publication from it.
10. Keep control-plane transitions deterministic and conversational design decisions visible to the user.
11. Serialize every task mutation with a process lock and commit multi-field recovery decisions in one state revision.
12. Run acceptance commands with network disabled, a controller-owned environment allowlist, reads denied for enumerated host credential paths, and filesystem writes limited to the recorded worktree plus a controller-owned per-command scratch directory.
13. Start nested Codex sessions with an isolated temporary `CODEX_HOME`; carry authentication only, not user instructions, skills, plugins, rules, or configuration. Give the model shell a separate temporary HOME and deny reads to the linked and resolved source authentication paths plus enumerated SSH, cloud, package-manager, and keychain credential paths.
14. Hash every passed step and final evidence directory. Recheck path containment, symlink absence, file presence, and the hash before trusting persisted success or reporting ready status.
15. Hold a non-blocking task execution lease in the control plane and a run-directory lock in the executor so concurrent invocations cannot share an evidence path or overwrite an index revision.
16. Run only on macOS and reject unsupported hosts before creating or mutating task or run state.
17. Freeze actionable failure evidence before launching a read-only Failure Analyst, and store analyst evidence under a separately hashed path.
18. Keep cross-task learning human-gated: create candidates after a verified retry, inject only active exact-signature lessons, and route SCV defects to proposal-only improvement work.

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
| `materialize TASK_ID [--worktree PATH] [--branch NAME] [--adopt-existing]` | Revalidate the base and create, recover, or explicitly adopt the isolated worktree |
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

The submitted file must use this exact JSON v1 shape. The control plane injects and fingerprints `expected_base_sha` when it stores the approved candidate, so omit that field from the draft.

```json
{
  "schema_version": 1,
  "task_id": "20260713-short-slug",
  "task": "Concise implementation outcome",
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

`timeout_seconds` and `final_acceptance` are optional. Every step needs at least one non-empty acceptance command. Do not put `git push` in any acceptance command. The ordered steps, not an undeclared dependency field, define execution order.

The submit command uses the executor's exact validator before changing task state. Unknown keys, boolean or out-of-range timeouts, mismatched task IDs or base revisions, and unsafe publication commands are rejected before plan approval or worktree creation.

Acceptance and final-acceptance commands run through `codex sandbox` with a controller-generated permission profile. The profile grants read access needed to run local tools, grants write access only to the recorded worktree and a controller-owned per-command scratch directory, denies the source Codex home and enumerated SSH, cloud, package-manager, Git credential, and keychain paths, and disables network and arbitrary Unix-socket access. Build the acceptance parent environment from a small allowlist so API keys, tokens, secrets, passwords, database URLs, askpass helpers, and cloud credentials are not inherited. A missing or unsupported sandbox is a blocker; the controller must never fall back to an unsandboxed shell. This is an exact sensitive-path boundary rather than a global user-home read denial, because macOS developer tools such as NVM-installed Node and pnpm may require read traversal under the user home.

## Failure behavior

- Preserve full command, exit status, and stderr for failed acceptance checks.
- Allow at most three persisted attempts per step. Exhaustion resumes at `PLANNING`, requires a materially revised and re-approved plan, and starts a new `runs/<plan-sha>/` evidence set without deleting the old one.
- Classify missing tools, invalid commands (including shell exit 126/127), stale base revisions, and state mismatches as blockers rather than implementation failures.
- Do not consume a step attempt when Codex, the sandbox, or a required executable cannot be launched. Preserve the blocker and retry only after the environment is corrected.
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

Before creating a worktree, persist its intended absolute path, branch, and approved base SHA. An existing worktree may be adopted only when either a previous SCV creation intent proves crash recovery or the user explicitly selected `--adopt-existing`. In both cases, require the exact branch identity, the approved base at `HEAD`, and a clean worktree. Reject unrelated, dirty, or merely name-matching worktrees.

## Runtime boundary

The supported local runtime is macOS with Git, Python 3.9 or newer, Codex CLI 0.144.1 or newer, POSIX `sh`, and `fcntl`. Linux, WSL, and Windows are outside the contract. Reject an unsupported host before task or run state is created or mutated. A `full` preflight must also verify the required `codex exec` and `codex sandbox` flags and start the fail-closed macOS acceptance sandbox. In a managed Codex session, invoke `start full`, `plan`-to-`full` promotion, `materialize`, and `execute` with host execution approval so the controller can own the nested Seatbelt boundary. This approval lifts only the outer controller invocation; it must not disable or widen the controller's inner worker, analyst, verifier, or acceptance sandboxes.
