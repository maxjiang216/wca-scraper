#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS="${GREEN}✓${NC}"
FAIL="${RED}✗${NC}"
SKIP="${YELLOW}·${NC}"

passed=0
failed=0
skipped=0

FIX=false
LANG_FILTER=""

usage() {
  echo "Usage: $0 [--fix] [--lang python|cpp|rust|js|go|bash]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fix) FIX=true; shift ;;
    --lang) LANG_FILTER="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CS="${REPO_ROOT}/.code-standards"

if [[ ! -d "${CS}" ]]; then
  echo "No .code-standards/ found; running bootstrap --configs-only..."
  bash "${SCRIPT_DIR}/bootstrap.sh" --configs-only
fi

run_check() {
  local label="$1"
  shift
  if "$@"; then
    echo -e "${PASS} ${label}"
    ((passed++))
  else
    echo -e "${FAIL} ${label}"
    ((failed++))
  fi
}

skip_tool() {
  local tool="$1"
  local install_hint="$2"
  echo -e "${SKIP} ${tool} not found — install with: ${install_hint}"
  ((skipped++))
}

gofmt_check() {
  local files
  files=$(gofmt -l .)
  if [[ -n "${files}" ]]; then
    echo "${files}"
    return 1
  fi
}

cd "${REPO_ROOT}"

# Precompute which languages are active so no function is called inside an
# if-condition (shellcheck SC2310).
DO_PYTHON=false; DO_CPP=false; DO_RUST=false
DO_JS=false;     DO_GO=false;  DO_BASH=false
if [[ -z "${LANG_FILTER}" || "${LANG_FILTER}" == "python" ]]; then DO_PYTHON=true; fi
if [[ -z "${LANG_FILTER}" || "${LANG_FILTER}" == "cpp"    ]]; then DO_CPP=true;    fi
if [[ -z "${LANG_FILTER}" || "${LANG_FILTER}" == "rust"   ]]; then DO_RUST=true;   fi
if [[ -z "${LANG_FILTER}" || "${LANG_FILTER}" == "js"     ]]; then DO_JS=true;     fi
if [[ -z "${LANG_FILTER}" || "${LANG_FILTER}" == "go"     ]]; then DO_GO=true;     fi
if [[ -z "${LANG_FILTER}" || "${LANG_FILTER}" == "bash"   ]]; then DO_BASH=true;   fi

# ── Python ────────────────────────────────────────────────────────────────────
if [[ "${DO_PYTHON}" == true ]]; then
  mapfile -t PY_FILES < <(find . -name "*.py" \
    -not -path "./.git/*" -not -path "./.code-standards/*" 2>/dev/null)
  if [[ "${#PY_FILES[@]}" -gt 0 ]]; then
    if command -v ruff > /dev/null 2>&1; then
      if [[ "${FIX}" == true ]]; then
        run_check "python: ruff format" \
          ruff format --config "${CS}/python/ruff.toml" .
        run_check "python: ruff lint --fix" \
          ruff check --fix --config "${CS}/python/ruff.toml" .
      else
        run_check "python: ruff format check" \
          ruff format --check --config "${CS}/python/ruff.toml" .
        run_check "python: ruff lint" \
          ruff check --config "${CS}/python/ruff.toml" .
      fi
    else
      skip_tool "ruff" "pip install ruff"
    fi
    if command -v mypy > /dev/null 2>&1; then
      run_check "python: mypy" mypy --config-file "${CS}/python/mypy.ini" .
    else
      skip_tool "mypy" "pip install mypy"
    fi
  fi
fi

# ── C++ ───────────────────────────────────────────────────────────────────────
if [[ "${DO_CPP}" == true ]]; then
  mapfile -t CPP_FILES < <(git ls-files \
    "*.c" "*.cc" "*.cpp" "*.cxx" "*.h" "*.hh" "*.hpp" "*.hxx" 2>/dev/null)
  if [[ "${#CPP_FILES[@]}" -gt 0 ]]; then
    if command -v clang-format > /dev/null 2>&1; then
      if [[ "${FIX}" == true ]]; then
        run_check "cpp: clang-format" \
          clang-format -i \
            --style="file:${CS}/cpp/.clang-format" \
            "${CPP_FILES[@]}"
      else
        run_check "cpp: clang-format check" \
          clang-format --dry-run --Werror \
            --style="file:${CS}/cpp/.clang-format" \
            "${CPP_FILES[@]}"
      fi
    else
      skip_tool "clang-format" "sudo apt-get install clang-format"
    fi

    if command -v clang-tidy > /dev/null 2>&1; then
      compile_db=""
      [[ -f compile_commands.json ]] && compile_db="."
      [[ -f build/compile_commands.json ]] && compile_db="build"
      if [[ -z "${compile_db}" ]]; then
        echo -e "${SKIP} clang-tidy — no compile_commands.json found"
        ((skipped++))
      else
        mapfile -t SRC_FILES < <(git ls-files \
          "*.c" "*.cc" "*.cpp" "*.cxx" 2>/dev/null)
        if [[ "${#SRC_FILES[@]}" -gt 0 ]]; then
          run_check "cpp: clang-tidy" \
            clang-tidy -p "${compile_db}" \
              --config-file "${CS}/cpp/.clang-tidy" \
              "${SRC_FILES[@]}"
        fi
      fi
    else
      skip_tool "clang-tidy" "sudo apt-get install clang-tidy"
    fi

    if command -v cppcheck > /dev/null 2>&1; then
      run_check "cpp: cppcheck" \
        cppcheck --enable=all --inline-suppr --error-exitcode=1 \
          --suppress=unmatchedSuppression \
          --suppressions-list="${CS}/cpp/cppcheck.suppressions" \
          "${CPP_FILES[@]}"
    else
      skip_tool "cppcheck" "sudo apt-get install cppcheck"
    fi
  fi
fi

# ── Rust ──────────────────────────────────────────────────────────────────────
if [[ "${DO_RUST}" == true ]] && [[ -f Cargo.toml ]]; then
  if command -v cargo > /dev/null 2>&1; then
    if [[ "${FIX}" == true ]]; then
      run_check "rust: rustfmt" \
        cargo fmt --all -- --config-path "${CS}/rust/rustfmt.toml"
    else
      run_check "rust: rustfmt check" \
        cargo fmt --all -- --check --config-path "${CS}/rust/rustfmt.toml"
    fi
    cp "${CS}/rust/clippy.toml" clippy.toml
    run_check "rust: clippy" \
      cargo clippy --all-targets --all-features \
        -- -D warnings \
           -W clippy::pedantic \
           -W clippy::nursery \
           -A clippy::module_name_repetitions
  else
    skip_tool "cargo" "https://rustup.rs"
  fi
fi

# ── JavaScript / TypeScript ───────────────────────────────────────────────────
if [[ "${DO_JS}" == true ]]; then
  mapfile -t JS_FILES < <(git ls-files \
    "*.ts" "*.tsx" "*.js" "*.jsx" 2>/dev/null)
  if [[ "${#JS_FILES[@]}" -gt 0 ]]; then
    if command -v npx > /dev/null 2>&1; then
      if [[ "${FIX}" == true ]]; then
        run_check "js: prettier" \
          npx prettier --write . \
            --config "${CS}/javascript/prettier.config.cjs"
      else
        run_check "js: prettier check" \
          npx prettier --check . \
            --config "${CS}/javascript/prettier.config.cjs"
      fi
      run_check "js: eslint" \
        npx eslint . --config "${CS}/javascript/eslint.config.mjs"
      run_check "js: tsc" \
        npx tsc --noEmit --project "${CS}/javascript/tsconfig.lint.json"
    else
      skip_tool "npx" "install Node.js from https://nodejs.org"
    fi
  fi
fi

# ── Go ────────────────────────────────────────────────────────────────────────
if [[ "${DO_GO}" == true ]] && [[ -f go.mod ]]; then
  if command -v gofmt > /dev/null 2>&1; then
    if [[ "${FIX}" == true ]]; then
      mapfile -t GO_FILES < <(find . -name "*.go" -not -path "./.git/*" 2>/dev/null)
      run_check "go: gofmt" gofmt -w "${GO_FILES[@]}"
    else
      run_check "go: gofmt check" gofmt_check
    fi
  else
    skip_tool "gofmt" "install Go from https://go.dev/dl"
  fi
  if command -v golangci-lint > /dev/null 2>&1; then
    run_check "go: golangci-lint" \
      golangci-lint run --config "${CS}/go/.golangci.yml" ./...
  else
    skip_tool "golangci-lint" \
      "go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest"
  fi
fi

# ── Bash ──────────────────────────────────────────────────────────────────────
if [[ "${DO_BASH}" == true ]]; then
  mapfile -t SH_FILES < <(git ls-files "*.sh" "*.bash" 2>/dev/null)
  if [[ "${#SH_FILES[@]}" -gt 0 ]]; then
    if command -v shellcheck > /dev/null 2>&1; then
      run_check "bash: shellcheck" \
        shellcheck --shell=bash --enable=all --exclude=SC2312 "${SH_FILES[@]}"
    else
      skip_tool "shellcheck" "sudo apt-get install shellcheck"
    fi
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "passed: ${GREEN}${passed}${NC}  failed: ${RED}${failed}${NC}  skipped: ${YELLOW}${skipped}${NC}"

[[ "${failed}" -eq 0 ]]
