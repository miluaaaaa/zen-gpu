#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: probe_via_job.sh NODE" >&2
  exit 1
fi

NODE_NAME="$1"
PROBE_GPUS="${ZEN_PROBE_GPUS:-2}"
job_id=""
probe_cleanup_enabled=0
PARTITION="${ZEN_PARTITION:?Set ZEN_PARTITION to a Slurm GPU partition}"
if [[ ! "${NODE_NAME}" =~ ^[a-zA-Z0-9_.-]+$ || ! "${PARTITION}" =~ ^[a-zA-Z0-9_.-]+$ ]]; then
  echo "[zen] invalid node or partition" >&2
  exit 2
fi

if [[ ! "${PROBE_GPUS}" =~ ^[0-9]+$ || "${PROBE_GPUS}" -lt 1 ]]; then
  echo "[probe_via_job] invalid ZEN_PROBE_GPUS=${PROBE_GPUS}" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="${ZEN_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/zen-gpu}/tmp"
mkdir -p "${TMP_DIR}"
STAMP="$(date +%Y%m%d_%H%M%S_%N)_$$"
PROBE_HOLD_SECONDS="${ZEN_PROBE_HOLD_SECONDS:-0}"
if [[ ! "${PROBE_HOLD_SECONDS}" =~ ^[0-9]+$ || "${PROBE_HOLD_SECONDS}" -gt 30 ]]; then
  echo "[probe_via_job] diagnostic hold must be between 0 and 30 seconds" >&2
  exit 1
fi
SBATCH_SCRIPT="${TMP_DIR}/probe_${NODE_NAME}_${STAMP}.sh"
REMOTE_TMP_DIR="${ZEN_REMOTE_ROOT:?Set an absolute ZEN_REMOTE_ROOT}/tmp"
REMOTE_SBATCH_SCRIPT="${REMOTE_TMP_DIR}/probe_${NODE_NAME}_${STAMP}.sh"
LOG_DIR="${ZEN_REMOTE_ROOT}/logs"
REMOTE_HOST="${ZEN_HOST:-}"
SSH_KEY="${ZEN_SSH_KEY:-}"
SSH_STRICT_HOST_KEY_CHECKING="${SSH_STRICT_HOST_KEY_CHECKING:-accept-new}"
PROBE_PENDING_TIMEOUT_SECONDS="${ZEN_PROBE_PENDING_TIMEOUT_SECONDS:-${ZEN_RUNTIME_PROBE_PENDING_TIMEOUT_SECONDS:-30}}"
PROBE_POLL_SECONDS="${ZEN_PROBE_POLL_SECONDS:-2}"
PROBE_TRACK_DIR="${ZEN_PROBE_TRACK_DIR:-}"
source "${SCRIPT_DIR}/node_state_lib.sh"

cleanup_probe_job_on_exit() {
  local rc=$?
  if [[ "${probe_cleanup_enabled}" == "1" && -n "${job_id}" ]]; then
    if probe_job_active "${job_id}"; then
      echo "[probe_via_job] interrupted/exit cleanup job_id=${job_id} node=${NODE_NAME}; scancel" >&2
      scancel_probe_job "${job_id}"
      write_probe_tracking "${job_id}" "CANCELLED_BY_LOCAL_EXIT" "${submitted_at_epoch:-}"
    fi
  fi
  exit "${rc}"
}

build_ssh_cmd() {
  local -a cmd
  cmd=(
    ssh
    -o BatchMode=yes
    -o ServerAliveInterval=30
    -o ServerAliveCountMax=3
  )
  if [[ -n "${SSH_KEY}" && -f "${SSH_KEY}" ]]; then
    cmd+=(-i "${SSH_KEY}")
  fi
  cmd+=(-o "StrictHostKeyChecking=${SSH_STRICT_HOST_KEY_CHECKING}")
  printf '%q ' "${cmd[@]}"
}

SSH_BASE="$(build_ssh_cmd)"

remote_exec() {
  if [[ "${ZEN_LOCAL:-0}" == "1" ]]; then
    bash -c "$*"
  else
    # shellcheck disable=SC2086
    ${SSH_BASE} "${REMOTE_HOST}" "$@"
  fi
}

submit_probe_job() {
  if [[ "${ZEN_LOCAL:-0}" == "1" ]]; then
    mkdir -p "${LOG_DIR}"
    sbatch --parsable "${SBATCH_SCRIPT}" | tail -n 1
    return
  fi

  remote_exec "mkdir -p '${REMOTE_TMP_DIR}' '${LOG_DIR}'"
  remote_exec "cat > '${REMOTE_SBATCH_SCRIPT}' <<'EOF'
$(cat "${SBATCH_SCRIPT}")
EOF
chmod +x '${REMOTE_SBATCH_SCRIPT}'"
  remote_exec "sbatch --parsable '${REMOTE_SBATCH_SCRIPT}'" | tail -n 1 | tr -d '\r'
}

probe_job_active() {
  local target_job_id="$1"
  if [[ "${ZEN_LOCAL:-0}" == "1" ]]; then
    squeue -j "${target_job_id}" -h 2>/dev/null | grep -q .
    return
  fi
  remote_exec "squeue -j '${target_job_id}' -h" 2>/dev/null | grep -q .
}

probe_job_queue_state() {
  local target_job_id="$1"
  local raw=""
  if [[ "${ZEN_LOCAL:-0}" == "1" ]]; then
    raw="$(squeue -j "${target_job_id}" -h -o '%T' 2>/dev/null | sed -n '1{s/^[[:space:]]*//;s/[[:space:]]*$//;p;q}' || true)"
  else
    raw="$(remote_exec "squeue -j '${target_job_id}' -h -o '%T' 2>/dev/null | sed -n '1{s/^[[:space:]]*//;s/[[:space:]]*$//;p;q}'" | tr -d '\r' || true)"
  fi
  printf '%s\n' "${raw:-UNKNOWN}"
}

scancel_probe_job() {
  local target_job_id="$1"
  if [[ "${ZEN_LOCAL:-0}" == "1" ]]; then
    scancel "${target_job_id}" >/dev/null 2>&1 || true
    return
  fi
  remote_exec "scancel '${target_job_id}'" >/dev/null 2>&1 || true
}

probe_job_final_state() {
  local target_job_id="$1"
  local raw=""
  if [[ "${ZEN_LOCAL:-0}" == "1" ]]; then
    raw="$(sacct -j "${target_job_id}" --format=State -n -X 2>/dev/null | sed -n '1{s/^[[:space:]]*//;s/[[:space:]].*$//;p;q}' || true)"
  else
    raw="$(remote_exec "sacct -j '${target_job_id}' --format=State -n -X 2>/dev/null | sed -n '1{s/^[[:space:]]*//;s/[[:space:]].*$//;p;q}'" | tr -d '\r' || true)"
  fi
  raw="${raw%%+*}"
  printf '%s\n' "${raw:-UNKNOWN}"
}

probe_track_path() {
  local target_job_id="$1"
  [[ -n "${PROBE_TRACK_DIR}" ]] || return 1
  printf '%s/probe_%s.env\n' "${PROBE_TRACK_DIR}" "${target_job_id}"
}

write_probe_tracking() {
  local target_job_id="$1"
  local status="$2"
  local submitted_at="${3:-}"
  local updated_at
  [[ -n "${PROBE_TRACK_DIR}" ]] || return 0
  mkdir -p "${PROBE_TRACK_DIR}" 2>/dev/null || return 0
  updated_at="$(date '+%Y-%m-%d %H:%M:%S %Z')"
  cat > "$(probe_track_path "${target_job_id}")" <<EOF
JOB_ID=${target_job_id}
NODE_NAME=${NODE_NAME}
PARTITION=${PARTITION}
PROBE_GPUS=${PROBE_GPUS}
STATUS=${status}
SUBMITTED_AT_EPOCH=${submitted_at:-}
UPDATED_AT='${updated_at}'
EOF
}

probe_log_path() {
  local ext="$1"
  printf '%s/probe_%s-%s.%s\n' "${LOG_DIR}" "${NODE_NAME}" "${job_id}" "${ext}"
}

read_probe_log() {
  local path="$1"
  if [[ "${ZEN_LOCAL:-0}" == "1" ]]; then
    cat "${path}" 2>/dev/null || true
    return
  fi
  remote_exec "cat '${path}'" 2>/dev/null || true
}

read_scheduler_snapshot() {
  local raw
  raw="$(remote_exec "scontrol show node '${NODE_NAME}'" 2>/dev/null || true)"
  if [[ -z "${raw}" ]]; then
    printf 'unknown||0|0\n'
    return
  fi

  local sched_state sched_reason cfg_tres alloc_tres
  sched_state="$(printf '%s\n' "${raw}" | grep -o 'State=[^[:space:]]*' | head -n 1 | cut -d= -f2)"
  sched_reason="$(printf '%s\n' "${raw}" | grep -o 'Reason=[^[:space:]]*' | head -n 1 | cut -d= -f2 || true)"
  cfg_tres="$(printf '%s\n' "${raw}" | grep -o 'CfgTRES=[^[:space:]]*' | head -n 1 | cut -d= -f2-)"
  alloc_tres="$(printf '%s\n' "${raw}" | grep -o 'AllocTRES=[^[:space:]]*' | head -n 1 | cut -d= -f2-)"
  printf '%s|%s|%s|%s\n' \
    "${sched_state:-unknown}" \
    "${sched_reason:-}" \
    "$(parse_tres_gpu_count "${alloc_tres}")" \
    "$(parse_tres_gpu_count "${cfg_tres}")"
}

cat > "${SBATCH_SCRIPT}" <<EOF
#!/usr/bin/env bash
#SBATCH -J probe_${NODE_NAME}
#SBATCH -p ${PARTITION}
#SBATCH -w ${NODE_NAME}
#SBATCH --gres=gpu:${PROBE_GPUS}
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH -t 00:02:00
#SBATCH -o ${LOG_DIR}/%x-%j.out
#SBATCH -e ${LOG_DIR}/%x-%j.err

set -euo pipefail
export ZEN_READY_MIN_FREE_MIB=${ZEN_READY_MIN_FREE_MIB:-30000}
echo "probe_node=${NODE_NAME}"
echo "probe_time=\$(date '+%F %T %Z')"
echo "[nvidia-smi]"
nvidia-smi || true
echo
echo "[query-gpu]"
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits || true
echo
echo "[query-compute-apps]"
nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv,noheader,nounits || true
echo
echo "[machine]"
python3 - <<'PY'
import subprocess
import os

def run(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return None

ready_min_free_mib = int(os.environ.get("ZEN_READY_MIN_FREE_MIB", "30000"))

gpu_raw = run([
    "nvidia-smi",
    "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,uuid",
    "--format=csv,noheader,nounits",
])
proc_raw = run([
    "nvidia-smi",
    "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
    "--format=csv,noheader,nounits",
])

occupied = "no"
ready_uuids = []
probed_uuids = []
process_uuids = set()
for line in (proc_raw or "").splitlines():
    line = line.strip()
    if not line or "No running processes found" in line:
        continue
    process_uuids.add(line.split(",")[0].strip())
    print("MACHINE_PROC|" + line)

for line in (gpu_raw or "").splitlines():
    parts = [item.strip() for item in line.split(",")]
    if len(parts) != 6:
        continue
    idx, name, used, total, util, uuid = parts
    try:
        used_i, total_i, util_i = int(used), int(total), int(util)
    except ValueError:
        continue
    probed_uuids.append(uuid)
    has_process = uuid in process_uuids
    if used_i > 0 or util_i > 0 or has_process:
        occupied = "yes"
    # Spare memory on a GPU with another model PID is not an exclusive lane.
    if proc_raw is not None and not has_process and util_i == 0 and total_i - used_i >= ready_min_free_mib:
        ready_uuids.append(uuid)
    print(f"MACHINE_GPU|{idx}|{name}|{used}|{total}|{util}")

if not probed_uuids or proc_raw is None:
    occupied = "unknown"
print(f"MACHINE_OCCUPIED|{occupied}")
print(f"MACHINE_READY_COUNT|{len(ready_uuids)}")
print(f"MACHINE_PROBED_COUNT|{len(probed_uuids)}")
print("MACHINE_PROBED_UUIDS|" + ",".join(probed_uuids))
print("MACHINE_READY_UUIDS|" + ",".join(ready_uuids))
PY
sleep ${PROBE_HOLD_SECONDS}
EOF

chmod +x "${SBATCH_SCRIPT}"
echo "[probe_via_job] submit node=${NODE_NAME} partition=${PARTITION}"
job_id="$(submit_probe_job)"
job_id="${job_id//$'\r'/}"
if [[ -z "${job_id}" ]]; then
  echo "[probe_via_job] failed to submit probe job" >&2
  exit 1
fi

echo "Submitted batch job ${job_id}"
echo "job_id=${job_id}"
submitted_at_epoch="$(date +%s)"
probe_cleanup_enabled=1
trap cleanup_probe_job_on_exit EXIT
write_probe_tracking "${job_id}" "submitted" "${submitted_at_epoch}"
echo "[probe_via_job] polling every ${PROBE_POLL_SECONDS}s until completion"
while probe_job_active "${job_id}"; do
  queue_state="$(probe_job_queue_state "${job_id}")"
  write_probe_tracking "${job_id}" "${queue_state}" "${submitted_at_epoch}"
  if [[ "${queue_state}" == "PENDING" && "${PROBE_PENDING_TIMEOUT_SECONDS}" =~ ^[0-9]+$ && "${PROBE_PENDING_TIMEOUT_SECONDS}" -gt 0 ]]; then
    now_epoch="$(date +%s)"
    if (( now_epoch - submitted_at_epoch >= PROBE_PENDING_TIMEOUT_SECONDS )); then
      echo "[probe_via_job] pending timeout job_id=${job_id} node=${NODE_NAME} timeout=${PROBE_PENDING_TIMEOUT_SECONDS}s; scancel" >&2
      scancel_probe_job "${job_id}"
      sleep 1
      scheduler_snapshot="$(read_scheduler_snapshot)"
      IFS='|' read -r sched_state sched_reason sched_alloc_gpus sched_total_gpus <<< "${scheduler_snapshot}"
      probed_at="$(date +%s)"
      write_node_state "${NODE_NAME}" \
        "NODE_NAME=${NODE_NAME}" \
        "PARTITION=${PARTITION}" \
        "PROBED_AT=${probed_at}" \
        "JOB_ID=${job_id:-unknown}" \
        "FINAL_STATE=PENDING_TIMEOUT" \
        "OCCUPIED=unknown" \
        "GPU_MIN_FREE_MIB=0" \
        "GPU_MAX_USED_MIB=0" \
        "GPU_READY_COUNT=0" \
        "PROBE_METHOD=slurm_job_pending_timeout" \
        "SCHED_STATE=${sched_state:-unknown}" \
        "SCHED_REASON=${sched_reason:-}" \
        "SCHED_ALLOC_GPUS=${sched_alloc_gpus:-0}" \
        "SCHED_TOTAL_GPUS=${sched_total_gpus:-0}"
      write_probe_tracking "${job_id}" "PENDING_TIMEOUT" "${submitted_at_epoch}"
      echo "[probe_via_job] state saved -> $(node_state_path "${NODE_NAME}")"
      exit 124
    fi
  fi
  sleep "${PROBE_POLL_SECONDS}"
done

final_state="UNKNOWN"
for _ in $(seq 1 15); do
  final_state="$(probe_job_final_state "${job_id}")"
  case "${final_state}" in
    ""|UNKNOWN|PENDING|RUNNING|CONFIGURING|COMPLETING)
      ;;
    *)
      break
      ;;
  esac
  if [[ "${final_state}" == "UNKNOWN" ]]; then
    :
  else
    echo "[probe_via_job] accounting state not terminal yet: job_id=${job_id} state=${final_state}" >&2
  fi
  sleep 2
done
if [[ -z "${final_state}" ]]; then
  final_state="UNKNOWN"
fi
case "${final_state}" in
  PENDING|RUNNING|CONFIGURING|COMPLETING)
    echo "[probe_via_job] accounting state remained transient: job_id=${job_id} state=${final_state}; will inspect probe output" >&2
    ;;
esac
echo "job_id=${job_id} final_state=${final_state}"
write_probe_tracking "${job_id}" "${final_state}" "${submitted_at_epoch}"
probe_cleanup_enabled=0

out_log="$(probe_log_path out)"
err_log="$(probe_log_path err)"
out_content="$(read_probe_log "${out_log}")"
err_content="$(read_probe_log "${err_log}")"

if [[ "${final_state}" != "COMPLETED" ]]; then
  output_ready_count="$(printf '%s\n' "${out_content}" | sed -n 's/^MACHINE_READY_COUNT|//p' | tail -n 1)"
  output_occupied="$(printf '%s\n' "${out_content}" | sed -n 's/^MACHINE_OCCUPIED|//p' | tail -n 1)"
  if [[ "${output_ready_count}" =~ ^[0-9]+$ && "${output_ready_count}" -ge "${PROBE_GPUS}" && "${output_occupied:-unknown}" != "yes" ]]; then
    echo "[probe_via_job] accounting final_state=${final_state}, but probe output is complete and ready_count=${output_ready_count}; treating probe as COMPLETED" >&2
    final_state="COMPLETED"
    write_probe_tracking "${job_id}" "${final_state}" "${submitted_at_epoch}"
  fi
fi

if [[ "${final_state}" == "UNKNOWN" ]]; then
  for _ in $(seq 1 5); do
    final_state="$(probe_job_final_state "${job_id}")"
    if [[ -n "${final_state}" && "${final_state}" != "UNKNOWN" ]]; then
    break
  fi
  sleep 2
done
fi

echo "===== ${out_log} ====="
printf '%s\n' "${out_content}"
echo "===== ${err_log} ====="
printf '%s\n' "${err_content}"

occupied="$(printf '%s\n' "${out_content}" | sed -n 's/^MACHINE_OCCUPIED|//p' | tail -n 1)"
if [[ -z "${occupied}" ]]; then
  occupied="unknown"
fi
gpu_min_free_mib="$(printf '%s\n' "${out_content}" | awk -F'|' '
  /^MACHINE_GPU\|/ {
    used = $4 + 0
    total = $5 + 0
    free = total - used
    if (count == 0 || free < min_free) {
      min_free = free
    }
    count += 1
  }
  END {
    if (count == 0) {
      print 0
    } else {
      print min_free + 0
    }
  }'
)"
gpu_max_used_mib="$(printf '%s\n' "${out_content}" | awk -F'|' '
  /^MACHINE_GPU\|/ {
    used = $4 + 0
    if (used > max_used) {
      max_used = used
    }
  }
  END { print max_used + 0 }'
)"
gpu_ready_count="$(printf '%s\n' "${out_content}" | sed -n 's/^MACHINE_READY_COUNT|//p' | tail -n 1)"
if [[ -z "${gpu_ready_count}" ]]; then
  gpu_ready_count="0"
fi
gpu_probed_count="$(printf '%s\n' "${out_content}" | sed -n 's/^MACHINE_PROBED_COUNT|//p' | tail -n 1)"
gpu_probed_uuids="$(printf '%s\n' "${out_content}" | sed -n 's/^MACHINE_PROBED_UUIDS|//p' | tail -n 1)"
gpu_ready_uuids="$(printf '%s\n' "${out_content}" | sed -n 's/^MACHINE_READY_UUIDS|//p' | tail -n 1)"
scheduler_snapshot="$(read_scheduler_snapshot)"
IFS='|' read -r sched_state sched_reason sched_alloc_gpus sched_total_gpus <<< "${scheduler_snapshot}"
probed_at="$(date +%s)"

write_node_state "${NODE_NAME}" \
  "NODE_NAME=${NODE_NAME}" \
  "PARTITION=${PARTITION}" \
  "PROBED_AT=${probed_at}" \
  "JOB_ID=${job_id:-unknown}" \
  "FINAL_STATE=${final_state:-unknown}" \
  "OCCUPIED=${occupied}" \
  "GPU_MIN_FREE_MIB=${gpu_min_free_mib:-0}" \
  "GPU_MAX_USED_MIB=${gpu_max_used_mib:-0}" \
  "GPU_READY_COUNT=${gpu_ready_count:-0}" \
  "GPU_PROBED_COUNT=${gpu_probed_count:-0}" \
  "GPU_PROBED_UUIDS=${gpu_probed_uuids}" \
  "GPU_READY_UUIDS=${gpu_ready_uuids}" \
  "PROBE_METHOD=slurm_job" \
  "SCHED_STATE=${sched_state:-unknown}" \
  "SCHED_REASON=${sched_reason:-}" \
  "SCHED_ALLOC_GPUS=${sched_alloc_gpus:-0}" \
  "SCHED_TOTAL_GPUS=${sched_total_gpus:-0}"
echo "[probe_via_job] state saved -> $(node_state_path "${NODE_NAME}")"
if [[ "${final_state}" != "COMPLETED" ]]; then
  exit 1
fi
