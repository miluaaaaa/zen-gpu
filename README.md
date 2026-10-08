# Zen GPU

Find usable GPUs on Slurm clusters where scheduler availability and actual GPU
occupancy can disagree. Zen probes all currently scheduler-free cards through
short telemetry jobs, reports individual GPU UUIDs, and distinguishes a busy
subset from a fully busy allocation.

This is the portable GPU-discovery core extracted from a local Zen workflow.
Project-specific model launchers, experiment registries, credentials and cluster
configuration are not part of this release. MIT licensed; no Python packages
are required.

## Requirements

- Client: Python 3.10+, Bash, OpenSSH, and an existing SSH login to the cluster.
- Cluster: Slurm (`scontrol`, `sbatch`, `squeue`, `sacct`, `scancel`), Python 3.10+,
  `nvidia-smi`, and a job-visible directory for telemetry scripts and logs.
- NVIDIA GPU telemetry is allocation-scoped. MIG-specific routing is not supported.

## Run

```bash
git clone https://github.com/miluaaaaa/zen-gpu.git
cd zen-gpu
```

```bash
bin/zen --host my-cluster
bin/zen --host my-cluster -a
bin/zen --host my-cluster -a accelerator-a accelerator-b --json
bin/zen --local -a
```

The first command reads scheduler availability only. `-a` / `--again` submits
short telemetry jobs and inspects all currently scheduler-free GPUs on each
candidate node. An eight-card idle node is not classified from only two cards.
Nodes and partitions are discovered through Slurm; no machine names are hardcoded.

For a custom SSH key or a shared filesystem:

```bash
bin/zen --host my-cluster --ssh-key ~/.ssh/cluster_key \
  --remote-root /shared/my-user/zen-probe -a --json
```

By default, remote logs are stored under the remote user's
`~/.cache/zen-gpu`; use `--remote-root` if compute nodes do not share that home
directory. Local state and logs live in `${XDG_CACHE_HOME:-~/.cache}/zen-gpu`.
Remote paths must be absolute and contain no spaces or shell metacharacters.

Optional installation, retaining this checkout:

```bash
mkdir -p ~/.local/bin
ln -s "$(pwd)/bin/zen" ~/.local/bin/zen-gpu
zen-gpu --help
```

## Reading the result

| Status | Meaning |
| --- | --- |
| `ready` | Fresh telemetry identified clean GPUs in the probed allocation. |
| `partial_ready` | Some probed UUIDs are clean while other cards are occupied. |
| `busy` | Completed telemetry covered the scheduler-free count and found no clean card. |
| `scheduler_full` | Slurm currently has no unallocated GPU resource on this node. |
| `unverified` | Telemetry is missing, expired, incomplete, or the probe failed/timed out. |
| `unavailable` | Node is down, draining, failing, or under maintenance. |

A clean card must have no visible compute PID, zero sampled compute utilization,
and enough free memory. A process holding GPU memory is occupied even at 0%
utilization; spare memory on a card with an existing model PID does not make an
exclusive GPU lane. Missing process telemetry fails closed.

The default minimum free memory is 30,000 MiB. Adjust it to the workload:

```bash
bin/zen --host my-cluster -a --min-free-mib 12000 --pending-timeout 15
```

Probe jobs cancel themselves after the pending timeout (default 15 seconds);
running telemetry jobs have a two-minute Slurm time limit. Nodes are scanned
sequentially. A whole-free-set probe can time out if the scheduler changes or a
full allocation is unavailable: that node remains unverified, rather than being
called busy. This version does not automatically split such a probe into smaller
allocations. Environment variables `ZEN_HOST`, `ZEN_SSH_KEY`, `ZEN_REMOTE_ROOT`,
and `ZEN_CACHE_DIR` provide defaults for the corresponding locations.

Telemetry-only mode is a timestamped snapshot. Jobs release their allocation after
probing and do not guarantee that a later Slurm allocation selects the same UUIDs. Consumers must inspect the actual
allocated UUIDs and confirm exclusive GPU execution inside their workload job.
Cached clean UUIDs are historical measurements, not current reservations.

## Tests

```bash
python3 -m unittest discover -s tests -v
bash -n lib/probe_via_job.sh lib/node_state_lib.sh
```

Regression fixtures cover incomplete node visibility, mixed busy/clean GPUs,
PID-based exclusivity, telemetry failures, expired evidence, and automatic
full-free-set probe sizing. GitHub Actions runs these checks on pushes and PRs.

## Start a lane without releasing its GPU

```bash
bin/zen --host my-cluster accelerator-a accelerator-b \
  --run-sbatch /shared/project/lane.sbatch \
  --remote-root /shared/my-user/zen-launch --pending-timeout 15
```

`--run-sbatch` submits a single-GPU workload allocation directly, checks the
allocated physical GPU inside that job, and executes the existing script in the
**same job** when admission passes. There is no telemetry-job-to-workload-job
release window. Cached clean UUIDs do not authorize this launch. Only one lane
is launched per invocation; callers can invoke it concurrently for disjoint lanes.

The script must be job-visible Bash. Its `#SBATCH` resource directives are copied
into the wrapper; Zen overrides node, partition, node/task counts and GPU binding
for one exclusive GPU. Use scripts intended for one GPU, without conflicting
`--gpus*` or heterogeneous-job directives. `--output` and `--export` forward Slurm
log and environment settings. The existing working directory and exported
environment follow normal `sbatch` semantics.

The admission gate requires exactly one visible and allocated GPU, readable GPU
and process telemetry, no existing compute PID, zero sampled utilization, and
enough free memory. Missing telemetry or an occupied card stops the wrapper
before the workload. MIG and clusters without `SLURM_JOB_GPUS` fail closed.

The CLI returns JSON with `ADMITTED_NOT_GPU_VERIFIED` after admission, leaving
the running workload allocated. This status is **not a successful model result**:
the workload must still prove its actual model PID has model-sized GPU memory
and nonzero compute during a forward pass, cancel invalid runs, and verify its
outputs. The caller owns monitoring and cancellation after handoff.

An attempt that remains pending is cancelled after `--pending-timeout`, recorded
as `PENDING_TIMEOUT`, and the next candidate is tried. Missing gate evidence is
`ADMISSION_FAILED` or `ADMISSION_TIMEOUT` (default 60 seconds). Rejected or
interrupted attempts cancel only their own job. A lack of scheduler admission is
never reported as proof that all physical GPUs are busy.

### Retry by progress

Use `--retry-rounds 3 --retry-delay 10` to refresh candidate nodes and make up to
three bounded launch rounds. Defaults are one round and a ten-second delay.
Each attempt records its phase, round, raw error and retry policy:

| Phase | Retry behavior |
| --- | --- |
| `WAITING_ALLOCATION` | Try the next candidate immediately; no occupancy inference. |
| `CHECKING_ALLOCATION` | Release this attempt and briefly back off this candidate, while trying others. |
| `HANDED_OFF` | Stop retrying; the workload owner monitors model execution. |

Policy uses progress fields rather than matching error messages. Handoff means
admission passed; it does not prove model startup or GPU execution. Unknown
phases stop automatic retries. Transport/configuration exceptions also stop the
invocation for diagnosis. No retry automatically restarts an admitted workload.

## Coordinate independent workstreams

All participants must use the same login host and registry directory. Operations
run on that host under a file lock; atomic replacement protects concurrent writes.

```bash
bin/zen register --host my-cluster --peer-id project-a --project experiment-a \
  --ready 3 accelerator-a accelerator-b --job-id 12345
bin/zen peers --host my-cluster
bin/zen --host my-cluster --peer-id project-a --run-sbatch /shared/project/lane.sbatch
```

The registry defaults to the remote user's `~/.cache/zen-gpu/coordination`.
Override it with `--registry-root` / `ZEN_REGISTRY_ROOT`. Set `ZEN_PEER_ID` or
pass `--peer-id` to opt workloads into coordination. Register only ready,
validated work and its suitable nodes; `--ready 0` withdraws runnable demand.
Use `--thread-id` to attach a conversation identity and `--note` to share task
status or handoff information. Refresh registration at least every 180 seconds
while waiting. Existing jobs
can be imported with repeated `--job-id`; their ownership cannot be duplicated.

Peers with ready work and zero currently allocated GPUs get first access to
compatible candidate nodes. Within that group, least-recently granted peers
come first. Once all waiting peers have capacity or an admission attempt in
progress, additional GPUs remain unrestricted. There is no fixed GPU cap and
no preemption of existing jobs. A lack of suitable capacity cannot guarantee
one simultaneous GPU per peer.

Claims reserve a coordination turn, not a GPU. They expire after 120 seconds
unless attached to a scheduler-visible job. Binding occurs immediately after
submission; Slurm controls the real allocation and the in-job gate still checks
the physical GPU. Pending jobs never count as running GPUs. Stale requests stop
competing, but their running jobs remain visible. Registry counts are allocation
observations, not same-PID model execution evidence or accepted results.

Legacy dispatchers may call `can-dispatch` with one candidate node before adding
a new job. That advisory check does not hold a turn; `--peer-id --run-sbatch` is
the coordinated submission path that protects concurrent admission. Workstreams
without integration remain outside fair admission. After handoff the owner
continues its existing model execution checks and cancellation policy.

## Peer messages and receipts

```bash
bin/zen send --host my-cluster --peer-id project-a --to project-b \
  --text 'Please hand off the next compatible GPU after your current lane finishes.' \
  --request-id handoff-001
bin/zen inbox --host my-cluster --peer-id project-b --mark-read --json
bin/zen ack --host my-cluster --peer-id project-b --message-id MESSAGE_ID \
  --text 'Handled: next released lane will go through fair admission.'
bin/zen outbox --host my-cluster --peer-id project-a --include-acked --json
```

`send` targets a registered peer or `--to all`. Use `--message-file` for multiline
bodies, `--request-id` to deduplicate retries, and `--reply-to` for linked replies.
`inbox` returns up to 50 pending messages. Delivery, explicit reading (`--mark-read`)
and handling (`ack` with a receipt) have separate timestamps. The sender can
inspect receipts with `outbox --include-acked`. No observer marks messages read
or handled on an agent's behalf. Notifications report counts only.

`notifications` provides pending/unread counts for integration with an existing
agent frontend or supervisor. Notification consumers should prompt the original
agent to read its inbox at a safe turn boundary, then acknowledge only after
handling the request. Mailbox messages do not themselves execute commands,
change experiment ownership or cancel another peer's jobs. All peers share the
login user's trust boundary; peer IDs are attribution, not cryptographic identities.
Bodies and receipts are limited to 8000 UTF-8 bytes; each recipient may have at
most 200 unacknowledged messages. Acknowledged messages persist for receipt audit.
