#!/usr/bin/env bash
set -euo pipefail

# ========== 固定参数 ==========
DATASET_NAME="princeton-nlp/SWE-bench_Verified"
MAX_WORKERS=8
TIMEOUT=3600                     # 👈 单个测评最大时间（秒）
SPLIT=""                         # e.g. "--split test"
EXTRA_ARGS=""

# ====== 自动重试配置 ======
RETRY_MAX=8
RETRY_SLEEP_BASE=15
RETRY_SLEEP_CAP=600
# ============================

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <predictions_path_list.txt> [work_dir]"
  exit 2
fi

LIST_FILE="$1"
WORK_DIR="${2:-./swebench_batch_runs}"

[[ -f "$LIST_FILE" ]] || { echo "❌ list file not found: $LIST_FILE"; exit 1; }

mkdir -p "$WORK_DIR/logs"

# -----------------------------
# 从 predictions_path 提取 run_id
# 规则：
#   .../qwen2.5-72b-git61-swe-default/run04/preds.json
# => qwen2.5-72b-git61-swe-default__run04
# -----------------------------
extract_run_id() {
  local p="$1"
  local run_dir model_name run_name

  run_dir="$(dirname "$p")"                  # .../run04
  run_name="$(basename "$run_dir")"           # run04
  model_name="$(basename "$(dirname "$run_dir")")"

  echo "${model_name}__${run_name}"
}

# -----------------------------
# 判断是否是“可重试错误”
# -----------------------------
is_retryable_log() {
  grep -Eqi \
    "Network is unreachable|ConnectionError|MaxRetryError|Max retries exceeded|Temporary failure|timed out|TLS|EOF|Connection reset|RemoteDisconnected|429|5[0-9]{2}" \
    "$1"
}

run_with_retries() {
  local run_id="$1"
  local pred_path="$2"
  local log="$3"

  local attempt=0
  local total=$((RETRY_MAX + 1))

  while (( attempt < total )); do
    attempt=$((attempt + 1))
    echo "     attempt ${attempt}/${total}"

    set +e
    (
      cd "$WORK_DIR"
      python -m swebench.harness.run_evaluation \
        --dataset_name "$DATASET_NAME" \
        --predictions_path "$pred_path" \
        --max_workers "$MAX_WORKERS" \
        --timeout "$TIMEOUT" \
        --run_id "$run_id" \
        $SPLIT \
        $EXTRA_ARGS
    ) 2>&1 | tee -a "$log"
    rc=${PIPESTATUS[0]}
    set -e

    [[ $rc -eq 0 ]] && return 0

    if is_retryable_log "$log" && (( attempt < total )); then
      sleep_s=$((RETRY_SLEEP_BASE * (2 ** (attempt - 1))))
      (( sleep_s > RETRY_SLEEP_CAP )) && sleep_s=$RETRY_SLEEP_CAP
      echo "     ⚠️ retryable error, sleep ${sleep_s}s"
      sleep "$sleep_s"
    else
      return "$rc"
    fi
  done

  return 1
}

# =============================
# 主循环
# =============================
idx=0
while IFS= read -r line || [[ -n "$line" ]]; do
  p="$(echo "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  [[ -z "$p" || "$p" == \#* ]] && continue

  idx=$((idx + 1))

  [[ -f "$p" ]] || { echo "[$idx] ⚠️ not found: $p"; continue; }

  run_id="$(extract_run_id "$p")"

  done_flag="$WORK_DIR/${run_id}.DONE"
  fail_flag="$WORK_DIR/${run_id}.FAIL"
  log="$WORK_DIR/logs/${run_id}.log"

  if [[ -f "$done_flag" ]]; then
    echo "[$idx] ✅ already done: $run_id"
    continue
  fi

  rm -f "$fail_flag"

  echo "[$idx] ▶ running $run_id"
  echo "     preds: $p"

  echo "===== $(date -Is) START $run_id =====" >> "$log"

  if run_with_retries "$run_id" "$p" "$log"; then
    date -Is > "$done_flag"
    echo "[$idx] ✅ DONE $run_id"
  else
    date -Is > "$fail_flag"
    echo "[$idx] ❌ FAIL $run_id"
  fi

  echo
done < "$LIST_FILE"

echo "🎉 All evaluations finished"
