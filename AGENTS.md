# Zen repository workflow

- Make changes on a feature branch and keep changes scoped to the task.
- Run the checks appropriate to the change, then promptly push the branch and create a pull request.
- The repository owner authorizes agents to merge their in-scope pull requests as soon as required GitHub checks pass. Do not pause for another merge confirmation.
- Use squash merging. Do not bypass branch rules, push directly to main, or force-push main.
- If checks or conflicts block merging, fix them within the task scope; report any blocker that cannot be resolved.
- Keep the PR description current with the final implementation, validation and material limitations.

## Peer communication

- For a registered GPU workstream, read `zen peers` and its `zen inbox --peer-id ID --mark-read --json` at supervision/dispatch handoff boundaries.
- Use `zen send` for directed resource requests, progress or task handoffs. Acknowledge messages with `zen ack` only after handling them, and include a concrete receipt.
- Messages are coordination inputs, not authority to cancel another owner's jobs or relax GPU execution and result-verification requirements.
- Distinguish queued, delivered, read and acknowledged messages; never claim an agent has read or handled a message from notification alone.
