#!/usr/bin/env bash
set -euo pipefail

# 사용 예시:
# LLAMA_CPP_DIR=/path/to/llama.cpp \
#   bash train/ax7b/export_ax7b_gguf.sh \
#   artifacts/ax7b-planner-merged \
#   artifacts/ax7b-planner.gguf \
#   q4_k_m

MERGED_DIR="${1:-artifacts/ax7b-planner-merged}"
OUTFILE="${2:-artifacts/ax7b-planner.gguf}"
OUTTYPE="${3:-q4_k_m}"

if [[ -z "${LLAMA_CPP_DIR:-}" ]]; then
  echo "[ERROR] LLAMA_CPP_DIR 환경변수가 필요합니다."
  exit 1
fi

if [[ ! -d "${MERGED_DIR}" ]]; then
  echo "[ERROR] 병합 모델 디렉터리를 찾을 수 없습니다: ${MERGED_DIR}"
  exit 1
fi

CONVERTER="${LLAMA_CPP_DIR}/convert_hf_to_gguf.py"
if [[ ! -f "${CONVERTER}" ]]; then
  echo "[ERROR] convert_hf_to_gguf.py를 찾을 수 없습니다: ${CONVERTER}"
  exit 1
fi

mkdir -p "$(dirname "${OUTFILE}")"
python "${CONVERTER}" "${MERGED_DIR}" --outtype "${OUTTYPE}" --outfile "${OUTFILE}"
echo "[DONE] GGUF exported: ${OUTFILE}"

