#!/usr/bin/env bash
set -euo pipefail

ZEN_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_STATE_DIR="${ZEN_STATE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/zen-gpu/state}"

ensure_node_state_dir() {
  mkdir -p "${NODE_STATE_DIR}"
}

node_state_path() {
  local node_name="$1"
  printf '%s/%s.env\n' "${NODE_STATE_DIR}" "${node_name}"
}

node_summary_path() {
  printf '%s/summary.tsv\n' "${NODE_STATE_DIR}"
}

write_node_state() {
  local node_name="$1"
  shift
  local path
  ensure_node_state_dir
  path="$(node_state_path "${node_name}")"
  local tmp_path
  tmp_path="$(mktemp "${path}.tmp.XXXXXX")"
  local item
  for item in "$@"; do
    printf '%s\n' "${item}" >> "${tmp_path}"
  done
  mv -f "${tmp_path}" "${path}"
}

node_state_read_value() {
  local path="$1"
  local key="$2"
  local default_value="${3:-}"
  if [[ ! -f "${path}" ]]; then
    printf '%s\n' "${default_value}"
    return
  fi
  local line
  line="$(grep -E "^${key}=" "${path}" | tail -n 1 || true)"
  if [[ -z "${line}" ]]; then
    printf '%s\n' "${default_value}"
    return
  fi
  printf '%s\n' "${line#*=}"
}

parse_tres_gpu_count() {
  local text="${1:-}"
  if [[ "${text}" =~ gres/gpu=([0-9]+) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return
  fi
  if [[ "${text}" =~ gpu:[^:[:space:]]+:([0-9]+) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return
  fi
  printf '0\n'
}

gpu_min_free_mib_for_ready() {
  local configured="${ZEN_READY_MIN_FREE_MIB:-30000}"
  if [[ "${configured}" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "${configured}"
    return
  fi
  printf '30000\n'
}

classify_node_readiness() {
  local path="$1"
  local max_age_seconds="$2"
  local gpu_cap="${3:-}"
  local ready_gpu_need="${4:-2}"

  if [[ ! -f "${path}" ]]; then
    printf 'unverified\n'
    return
  fi

  local now_epoch effective_gpu_limit
  now_epoch="$(date +%s)"
  local probed_at final_state occupied sched_state sched_reason sched_alloc_gpus sched_total_gpus gpu_min_free_mib gpu_ready_count ready_min_free_mib
  probed_at="$(node_state_read_value "${path}" "PROBED_AT" "0")"
  final_state="$(node_state_read_value "${path}" "FINAL_STATE" "unknown")"
  occupied="$(node_state_read_value "${path}" "OCCUPIED" "unknown")"
  sched_state="$(node_state_read_value "${path}" "SCHED_STATE" "unknown")"
  sched_reason="$(node_state_read_value "${path}" "SCHED_REASON" "")"
  sched_alloc_gpus="$(node_state_read_value "${path}" "SCHED_ALLOC_GPUS" "0")"
  sched_total_gpus="$(node_state_read_value "${path}" "SCHED_TOTAL_GPUS" "0")"
  gpu_min_free_mib="$(node_state_read_value "${path}" "GPU_MIN_FREE_MIB" "0")"
  gpu_ready_count="$(node_state_read_value "${path}" "GPU_READY_COUNT" "0")"
  ready_min_free_mib="$(gpu_min_free_mib_for_ready)"

  if [[ ! "${probed_at}" =~ ^[0-9]+$ ]]; then
    printf 'invalid_state_file\n'
    return
  fi

  local age=$(( now_epoch - probed_at ))
  if [[ "${sched_reason}" == *"Unavailable"* || "${sched_state}" == *"DOWN"* || "${sched_state}" == *"DRAIN"* || "${sched_state}" == *"NOT_RESPOND"* ]]; then
    printf 'unavailable\n'
    return
  fi
  effective_gpu_limit="${sched_total_gpus}"
  if [[ "${gpu_cap}" =~ ^[0-9]+$ && "${gpu_cap}" -gt 0 && "${sched_total_gpus}" =~ ^[0-9]+$ && "${sched_total_gpus}" -gt 0 && "${gpu_cap}" -lt "${sched_total_gpus}" ]]; then
    effective_gpu_limit="${gpu_cap}"
  fi
  if [[ "${effective_gpu_limit}" =~ ^[0-9]+$ && "${sched_alloc_gpus}" =~ ^[0-9]+$ && "${effective_gpu_limit}" -gt 0 && "${sched_alloc_gpus}" -ge "${effective_gpu_limit}" ]]; then
    if [[ "${occupied}" == "yes" ]]; then
      printf 'busy\n'
    else
      printf 'sched_full\n'
    fi
    return
  fi
  if [[ "${final_state}" == "COMPLETED" && "${age}" -gt "${max_age_seconds}" ]]; then
    printf 'stale\n'
    return
  fi
  if [[ "${occupied}" == "yes" ]]; then
    if [[ "${final_state}" == "COMPLETED" && "${gpu_ready_count}" =~ ^[0-9]+$ && "${gpu_ready_count}" -gt 0 ]]; then
      # Known clean UUIDs exist, but a node-only allocation may still select busy
      # cards. Report the subset without passing legacy whole-node ready gates.
      printf 'partial_ready\n'
      return
    fi
    local gpu_probed_count
    gpu_probed_count="$(node_state_read_value "${path}" "GPU_PROBED_COUNT" "0")"
    if [[ ! "${gpu_probed_count}" =~ ^[0-9]+$ || "${gpu_probed_count}" -lt $((sched_total_gpus - sched_alloc_gpus)) ]]; then
      printf 'unverified\n'
      return
    fi
    printf 'busy\n'
    return
  fi
  if [[ "${age}" -le "${max_age_seconds}" && "${gpu_ready_count}" =~ ^[0-9]+$ && "${gpu_ready_count}" -ge "${ready_gpu_need}" && "${occupied}" != "yes" ]]; then
    printf 'ready\n'
    return
  fi
  if [[ "${final_state}" == "COMPLETED" && "${age}" -gt "${max_age_seconds}" ]]; then
    printf 'stale\n'
    return
  fi
  if [[ "${sched_alloc_gpus}" =~ ^[0-9]+$ && "${sched_alloc_gpus}" -gt 0 ]]; then
    printf 'sched_busy\n'
    return
  fi
  printf 'unknown\n'
}
