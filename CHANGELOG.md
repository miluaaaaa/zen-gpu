# Changelog

## Unreleased

- Add `--run-sbatch`: check the allocated single GPU and start the existing
  workload in the same Slurm job, eliminating the release/resubmit race.
- Fail closed on occupied cards or unavailable admission evidence, distinguish
  pending allocation timeouts from physical occupancy, and cancel failed attempts.
- Preserve workload resource directives and keep model execution verification
  separate from GPU admission.

## 0.1.0 — 2026-10-08

- Probe all currently scheduler-free cards instead of assuming a two-card sample
  represents an entire node.
- Record probed counts, probed UUIDs, clean counts and clean UUIDs.
- Report clean subsets independently of occupied cards (`partial_ready`).
- Require PID-free GPU telemetry for exclusive readiness; fail closed when
  process visibility is unavailable.
- Preserve evidence age and expire cached measurements.
- Bound pending probe allocations, cancel unfinished probes on exit, and keep
  whole-node workload gates separate from partial-card observations.
- Provide a portable CLI, generic Slurm node/partition discovery, configurable
  SSH and storage locations, regression tests, and CI.
