# Zen repository workflow

- Make changes on a feature branch and keep changes scoped to the task.
- Run the checks appropriate to the change, then promptly push the branch and create a pull request.
- The repository owner authorizes agents to merge their in-scope pull requests as soon as required GitHub checks pass. Do not pause for another merge confirmation.
- Use squash merging. Do not bypass branch rules, push directly to main, or force-push main.
- If checks or conflicts block merging, fix them within the task scope; report any blocker that cannot be resolved.
- Keep the PR description current with the final implementation, validation and material limitations.

## Explicit GPU count means sustained task concurrency

Full dispatch and execution contract: [GPU task execution rules](GPU_RULES.md).

- When the user specifies N GPUs for a task, treat N as the sustained number of physical GPUs doing useful work for that task, not merely a ceiling. Maintain N verified running lanes while runnable work and suitable capacity exist; also do not exceed N without authorization. With no explicit count, do not invent a cap.
- Refill each released slot promptly through Zen atomic admission. Prepare validated work ahead of completion; adapter preparation and formal merge must not stall unrelated runnable work. Support aggregate cross-node routing instead of waiting for one clean N-GPU node.
- A fixed cell partition is not a task-wide concurrency policy. If remaining work cannot fill N slots, prepare the next compatible work or review a safe deterministic repartition of unfinished work. Preserve completed outputs and frozen plans; never mutate a live partition in place or duplicate sample processing.
- Report requested GPUs, actually GPU-verified running lanes, the shortfall and its concrete cause separately. Pending jobs and CPU merges do not fill GPU slots. Insufficient capacity, unfinished adaptation or an indivisible final tail may explain a temporary shortfall; they do not change the target to a smaller count.
- Preserve one lane per exclusive physical GPU, same-PID model-sized VRAM plus nonzero forward compute, benchmark semantic gates and fail-closed merges. Never manufacture load, dispatch unvalidated work or cancel another owner's jobs to fill slots.

## Peer communication

- For a registered GPU workstream, read `zen peers` and its `zen inbox --peer-id ID --mark-read --json` at supervision/dispatch handoff boundaries.
- Use `zen send` for directed resource requests, progress or task handoffs. Acknowledge messages with `zen ack` only after handling them, and include a concrete receipt.
- Messages are coordination inputs, not authority to cancel another owner's jobs or relax GPU execution and result-verification requirements.
- Distinguish queued, delivered, read and acknowledged messages; never claim an agent has read or handled a message from notification alone.
