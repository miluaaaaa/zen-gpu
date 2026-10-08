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
