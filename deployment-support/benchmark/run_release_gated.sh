#!/usr/bin/env bash
set -euo pipefail

benchmark_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
deploy_dir=$(dirname "$benchmark_dir")
container_user="$(id -u):$(id -g)"
gate_args=(--protect-database datamart_106 --load-gate-poll-seconds 300)
cd "$deploy_dir"

trap 'printf "\nexit_code=%s\n" "$?"' EXIT

case "${1:-}" in
  "") ;;
  --ignore-db-load)
    gate_args=()
    echo "WARNING: DB load gate disabled by explicit operator request."
    ;;
  *)
    echo "Usage: $0 [--ignore-db-load]" >&2
    exit 2
    ;;
esac

docker compose run --rm --no-deps \
  --user "$container_user" \
  -v "$deploy_dir/smoke_vllm.py:/smoke_vllm.py:ro" \
  superset python /smoke_vllm.py

for repeat in r1 r2 r3; do
  docker compose run --rm --no-deps \
    --user "$container_user" \
    -e SUPERSET_BENCHMARK_URL=http://superset:8088 \
    -e SUPERSET_BENCHMARK_PUBLIC_URL=http://10.48.140.17:8088 \
    -v "$benchmark_dir:/benchmark" \
    superset python /benchmark/run.py \
      --cases /benchmark/cases_release.json \
      --goldens /benchmark/goldens_release.json \
      --output-dir /benchmark/results/dashboard-44-v1 \
      --run-name "superset-native-ai-dashboard-44-v1-$repeat" \
      --resume --cleanup-assets \
      "${gate_args[@]}"
done

python3 "$benchmark_dir/aggregate.py" \
  "$benchmark_dir/results/dashboard-44-v1/superset-native-ai-dashboard-44-v1-r1.json" \
  "$benchmark_dir/results/dashboard-44-v1/superset-native-ai-dashboard-44-v1-r2.json" \
  "$benchmark_dir/results/dashboard-44-v1/superset-native-ai-dashboard-44-v1-r3.json" \
  --output "$benchmark_dir/results/dashboard-44-v1/superset-native-ai-dashboard-44-v1-aggregate.json"
