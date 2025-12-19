
cd ..
export ITERATIVE_EVAL_MODE=false

# 使用 GLM-4.6 作为 LLM，在 swe-bench-Verified 上 rollout 500 个实例
bash /data/yxhuang/OpenHands_copy/evaluation/benchmarks/swe_bench/scripts/run_infer.sh \
  llm.glm46_eval_1 \
  HEAD \
  CodeActAgent \
  500 \
  100 \
  32 \
  princeton-nlp/SWE-bench_Verified \
  test
