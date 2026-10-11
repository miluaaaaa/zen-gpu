# GPU task execution rules

## A user-specified GPU count is a maintained task target

When the user says “use eight GPUs to finish this task”, “keep N GPUs”, or
“limit this task to N GPUs”, keep N exclusively allocated physical GPUs doing
valid work for that same task while runnable work remains. Record the number
and its task scope. Do not silently turn it into a one-time allocation, a peak
measurement, or a global limit shared with unrelated tasks. Do not exceed an
explicit task limit without the user's authorization. Without an explicit
user count, use suitable available capacity subject to real technical and
scheduler constraints; do not invent a cap.

“八卡一起完成这个任务”的含义是：该任务有可运行工作时，持续让八张独占
物理 GPU 协同推进任务；任务中的作业完成、失败或被取消后，及时通过 Zen
派发下一份已预检的同任务工作，补回八卡。不能拿到八张后放任卡数下降，
也不能只把八卡作为瞬时峰值或八个排队名额。

## Partition, dispatch and refill

- For a scalable evaluation cell, use deterministic, disjoint input partitions
  across the N GPUs. Each lane reserves exactly one physical GPU, and each
  physical GPU executes at most one lane of this task at a time. A final merge
  must verify the required lane set and exact sample coverage. Learned lanes
  must synchronize training or consume the same explicitly shared checkpoint;
  independently trained lane models cannot be merged as one cell.
- Route suitable lanes across nodes through Zen. Do not require all N GPUs on
  one node or leave runnable lanes waiting for the full count. Same-node
  collectives or model sharding require a documented technical contract and
  an appropriate allocation; the ordinary single-GPU lane CLI does not provide
  that launch mode.
- A frozen cell partition does not redefine the task target. Prepare the next
  compatible task work or review a safe deterministic repartition of unfinished
  work; preserve completed outputs and never mutate a live partition in place.
- Maintain a bounded queue of preflighted task work. Reconcile active lanes at
  dispatch and supervision boundaries. After a completion, failure or
  cancellation, promptly refill the deficit with eligible work from that task.
  Never cancel another owner's job to meet this target.
- If fewer than N suitable GPUs are available, run eligible work on the GPUs
  obtained, continue bounded Zen discovery/routing, and report target, actual
  execution count, deficit and concrete blocker. Do not silently lower the
  target or claim it is satisfied. Pending allocations and telemetry probes do
  not count. Insufficient remaining work or a real scalability constraint must
  be reported rather than hidden with duplicate work or artificial GPU load.

## Execution evidence and completion

A scheduler allocation or admission is not model execution. Each running lane
must prove the actual model PID has model-sized GPU memory and nonzero GPU
compute during a forward pass on its allocated GPU. CPU fallback is forbidden.
If the model is CPU-saturated while its allocated GPU is idle after startup,
cancel the job, mark the run invalid and exclude its output. Missing same-PID
execution evidence blocks acceptance; repair the GPU path or cancel the run.

Report the maintained target separately from currently verified execution,
submitted/pending jobs, and completed validated results. Release allocations
when the task is complete, has no valid runnable work, or the user stops it.
Never hold idle GPUs merely to display N.

These are rules for the workload owner and dispatcher. Zen's `--run-sbatch`
launches one lane per invocation and hands monitoring to the caller; it does
not itself maintain N lanes or verify final model results. A maintained target
requires the caller to implement the reconciliation and refill loop.
