# Project working instructions

## Restore context from project files

- Start with `docs/PROJECT.md` and `docs/STATUS.json`, then run `python -X utf8 tools/project_status.py`.
- Read only the selected task in `docs/ROADMAP.md` and the input files it names. Expand context when needed; do not reload the full project or chat history by default.
- The user's latest explicit instruction overrides local plans. Preserve the budget constraint: this phase uses existing computer resources and does not require buying hardware, paid services, or deployment.
- `docs/STATUS.json` is the sole task-status record. `docs/PROJECT.md` records user requirements. `docs/DECISIONS.md` records rationale and provisional choices. Code and generated manifests describe the actual executable parameters.
- If these disagree, investigate the relevant evidence and update the files. Do not silently promote a previous assistant suggestion to a user requirement.

## Execute one bounded task

- The user has explicitly authorized separate project conversations for teamwork. For an assigned team conversation, first read `docs/TEAM.md` and its own assignment file. The team rules below specialize the single-main-task workflow: only the coordinator writes global status; workers use their assigned scope and report file instead of claiming `next_task`.

- For a request to continue development, use `next_task` unless the user directs another task. Check its dependencies and acceptance criteria first.
- Mark the task `in_progress` and set `active_task` before making task changes. Keep at most one active main task; do not infer authorization to spawn agents.
- Implement only the selected task and necessary dependencies. Routine reversible choices within its scope do not require another permission question.
- Record units, validity ranges, synthetic-data labels, seeds, and source parameters. A synthetic scenario is not a real garment experiment.
- Do not enable a real collection recommendation from repeated weak signals without independent calibration evidence. Keep current research states distinct from future product states.
- Run checks appropriate to changed behavior. Do not repeatedly run unrelated tests merely to refresh a report.
- If model sources change, regenerate the affected synthetic outputs and explain any changed conclusions. Do not report an old image or old passing test record as current evidence.
- Finish by recording task status, files changed, checks and limits, unresolved issues, and the exact next task in `docs/STATUS.json`. Append a decision entry only when a meaningful decision changes.
- A task is `done` only when its declared acceptance criteria have evidence. Otherwise retain `in_progress` and write a concrete `resume_action`; do not use a guessed success summary.
- Planning-only requests update the plan and handoff files without automatically executing the next implementation task.

## Team collaboration

- Use the assignment-specific write ownership in `docs/TEAM.md`. These conversations share the same checkout, so respect ownership even if another file appears easy to fix.
- Assigned workers do not modify `docs/STATUS.json`, `docs/DECISIONS.md`, `docs/ROADMAP.md`, `docs/PROJECT.md`, `docs/TEAM.md`, this file, root README, `tools/project_status.py`, or `docs/verification/latest-tests.json`. Propose changes in the assigned report for the coordinator.
- Workers read the frozen baseline named in the assignment when an input is being changed by another worker. Never treat an unreviewed file in another lane as a stable interface.
- Run tests for the owned work and record commands and results in the owned report. Only the coordinator records the main project test receipt after integration review.
- Prepare later-stage research independently, but do not mark dependent roadmap tasks complete before prerequisites are accepted.
- Do not create additional conversations/subagents, send messages to other conversations, commit, reset, or clean the shared checkout as part of a worker assignment. Submit through the assigned files and the conversation's final response; the coordinator reads them.

## Keep the project recoverable

- Do not write chat transcripts into project records. Preserve decisions, evidence, unknowns, and next actions instead.
- Keep the project brief concise. Keep detailed derivations in the theory files and reference them by path.
- Preserve user changes and existing untracked artifacts. Do not reset, clean, publish, or create cloud resources as part of context restoration.
- Older documents retain useful background; mark replaced guidance and link to the current record instead of treating every old sentence as a binding instruction.
- Do not update global assistant memory from this workflow. Project-local files are the handoff mechanism.

## Android physical-device safety

- Never use `adb shell monkey` to launch or test an app on a user's physical Android device. On HyperOS, Monkey injects rotation events that can clear the user's rotation lock.
- Resolve the launcher component with `adb shell cmd package resolve-activity --brief <package>` and launch it with `adb shell am start -W -n <component>`.
- Preserve the current `accelerometer_rotation` and `user_rotation` values around any test that intentionally changes device orientation.
