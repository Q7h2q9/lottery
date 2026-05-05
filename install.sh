#!/usr/bin/env bash
# Install multidraw + its agentflow dependency in a fresh venv.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
AGENTFLOW_REPO="${AGENTFLOW_REPO:-https://github.com/berabuddies/agentflow.git}"
AGENTFLOW_SRC="${AGENTFLOW_SRC:-${ROOT_DIR}/.deps/agentflow}"

echo "[multidraw] creating venv at ${VENV_DIR}"
python3 -m venv "${VENV_DIR}"
# shellcheck disable=SC1091
. "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip wheel

if [[ ! -d "${AGENTFLOW_SRC}/.git" ]]; then
  echo "[multidraw] cloning agentflow into ${AGENTFLOW_SRC}"
  mkdir -p "$(dirname "${AGENTFLOW_SRC}")"
  git clone --depth 1 "${AGENTFLOW_REPO}" "${AGENTFLOW_SRC}"
fi

echo "[multidraw] installing agentflow (editable)"
pip install -e "${AGENTFLOW_SRC}"

echo "[multidraw] installing multidraw (editable, with dev extras)"
pip install -e "${ROOT_DIR}[dev]"

cat <<EOF

[multidraw] done.

Activate the venv:
  source ${VENV_DIR}/bin/activate

Try the CLI:
  multidraw --help
  multidraw serve              # web UI on 127.0.0.1:8765
  multidraw new                # scaffold a project spec
  multidraw run examples/codegen_race.yaml

EOF
