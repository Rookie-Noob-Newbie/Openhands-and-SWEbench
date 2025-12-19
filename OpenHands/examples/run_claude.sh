
cd ..
export ITERATIVE_EVAL_MODE=false

# 使用 GLM-4.6 作为 LLM，在 swe-bench-Verified 上 rollout 500 个实例
bash evaluation/benchmarks/swe_bench/scripts/run_infer.sh \
  llm.claude45_eval \
  HEAD \
  CodeActAgent \
  5 \
  100 \
  5 \
  princeton-nlp/SWE-bench_Verified \
  test
