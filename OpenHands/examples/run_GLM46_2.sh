#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_DIR="${OPENHANDS_TMP_DIR:-$REPO_ROOT/tmp}"
TMP_DIR="${TMP_DIR/#\~/$HOME}"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

cd "$REPO_ROOT"
export ITERATIVE_EVAL_MODE=false

# 使用 GLM-4.6 作为 LLM，在 swe-bench-Verified 上 rollout 500 个实例
bash evaluation/benchmarks/swe_bench/scripts/run_infer.sh \
  llm.glm46_eval_2 \
  HEAD \
  CodeActAgent \
  500 \
  100 \
  32 \
  princeton-nlp/SWE-bench_Verified \
  test
 
