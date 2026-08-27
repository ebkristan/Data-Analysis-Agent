#!/usr/bin/env bash
# Build an unsigned macOS .app + .dmg from the audited PyInstaller onedir/app.
#
# This script must run on a native macOS runner. It deliberately mirrors the
# Windows build pipeline:
#   clean allowlisted staging -> staging audit -> PyInstaller -> app audit
#   -> frozen smoke -> DMG -> DMG/root audit -> release manifest

set -euo pipefail

VERSION="1.2.0"
WORK_ROOT=""
PREPARE_ONLY=0

usage() {
  cat <<'EOF'
Usage: bash packaging/build_macos.sh [--version X.Y.Z] [--work-root PATH] [--prepare-only]

Builds an unsigned macOS test package. Output defaults to:
  build/macos-package/dmg/BusinessAnalyticsAgent-macOS-<arch>.dmg

Options:
  --version       Semantic version written to release.json.
  --work-root     Build directory. Must stay below project build/.
  --prepare-only  Stop after audited .app and frozen self-test; do not create DMG.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    --work-root)
      WORK_ROOT="${2:-}"
      shift 2
      ;;
    --prepare-only)
      PREPARE_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]]; then
  echo "Version must look like 1.2.3 or 1.2.3-test.1" >&2
  exit 2
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS packages must be built on a native macOS runner." >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BUILD_ROOT="$PROJECT_ROOT/build"
if [[ -z "$WORK_ROOT" ]]; then
  WORK_ROOT="$BUILD_ROOT/macos-package"
fi
mkdir -p "$BUILD_ROOT"
WORK_ROOT="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$WORK_ROOT")"
BUILD_ROOT_REAL="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$BUILD_ROOT")"
case "$WORK_ROOT" in
  "$BUILD_ROOT_REAL"/*) ;;
  *)
    echo "Work root must stay below the project build directory." >&2
    exit 2
    ;;
esac

rm -rf "$WORK_ROOT"
STAGING="$WORK_ROOT/staging"
PYI_WORK="$WORK_ROOT/pyinstaller-work"
PYI_DIST="$WORK_ROOT/pyinstaller-dist"
APP="$PYI_DIST/Business Analytics Agent.app"
RUNTIME_SMOKE="$WORK_ROOT/runtime-smoke"
DMG_ROOT="$WORK_ROOT/dmg-root"
DMG_OUTPUT="$WORK_ROOT/dmg"
REPORTS="$WORK_ROOT/reports"
mkdir -p "$REPORTS" "$DMG_OUTPUT"

python3 "$PROJECT_ROOT/packaging/build_manifest.py" \
  --source "$PROJECT_ROOT" \
  --destination "$STAGING" \
  --manifest "$REPORTS/staging-manifest.json"
python3 "$PROJECT_ROOT/packaging/audit_artifact.py" \
  "$STAGING" \
  --report "$REPORTS/staging-audit.json"

export BAA_STAGING_ROOT="$STAGING"
python3 -m PyInstaller --clean --noconfirm \
  --distpath "$PYI_DIST" \
  --workpath "$PYI_WORK" \
  "$PROJECT_ROOT/packaging/business_agent.spec"

if [[ ! -d "$APP" ]]; then
  echo "PyInstaller did not produce the expected app bundle: $APP" >&2
  exit 2
fi
python3 "$PROJECT_ROOT/packaging/audit_artifact.py" \
  "$APP" \
  --allow-contained-symlinks \
  --report "$REPORTS/app-audit.json"

export BAA_DATA_DIR="$RUNTIME_SMOKE"
export BAA_NO_BROWSER=1
export BAA_ONEDIR_SELF_TEST=1
export BAA_CLEANUP_DISABLED=1
"$APP/Contents/MacOS/BusinessAnalyticsAgent"
SMOKE_REPORT="$RUNTIME_SMOKE/outputs/build-smoke.json"
if [[ ! -f "$SMOKE_REPORT" ]]; then
  echo "Frozen self-test did not create its report." >&2
  exit 2
fi
python3 - "$SMOKE_REPORT" <<'PY'
import json
import sys
report = json.loads(open(sys.argv[1], encoding="utf-8").read())
if not report.get("ok") or not report.get("frozen"):
    raise SystemExit("Frozen self-test report is not successful.")
PY
cp "$SMOKE_REPORT" "$REPORTS/frozen-smoke.json"
unset BAA_ONEDIR_SELF_TEST

if [[ "$PREPARE_ONLY" -eq 1 ]]; then
  echo "Audited macOS app ready: $APP"
  exit 0
fi

ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
  arm64) ARCH="arm64" ;;
  x86_64) ARCH="x64" ;;
  *) ARCH="$ARCH_RAW" ;;
esac
DMG_NAME="BusinessAnalyticsAgent-macOS-$ARCH.dmg"
DMG="$DMG_OUTPUT/$DMG_NAME"

mkdir -p "$DMG_ROOT"
ditto "$APP" "$DMG_ROOT/Business Analytics Agent.app"
python3 "$PROJECT_ROOT/packaging/audit_artifact.py" \
  "$DMG_ROOT" \
  --allow-contained-symlinks \
  --report "$REPORTS/dmg-root-audit.json"

create_dmg() {
  local attempt
  local log="$REPORTS/hdiutil-create.log"
  for attempt in 1 2 3; do
    rm -f "$DMG" "$DMG".*
    sync
    if hdiutil create \
      -volname "Business Analytics Agent" \
      -srcfolder "$DMG_ROOT" \
      -ov \
      -format UDZO \
      "$DMG" >"$log" 2>&1; then
      cat "$log"
      return 0
    fi
    cat "$log" >&2
    if [[ "$attempt" -eq 3 ]]; then
      hdiutil info >&2 || true
      if command -v lsof >/dev/null 2>&1; then
        lsof +D "$DMG_ROOT" >&2 || true
      fi
      return 1
    fi
    echo "hdiutil create failed on attempt $attempt; retrying..." >&2
    sleep $((attempt * 5))
  done
}

create_dmg
python3 "$PROJECT_ROOT/packaging/audit_artifact.py" \
  "$DMG" \
  --report "$REPORTS/dmg-audit.json"

SHA256="$(shasum -a 256 "$DMG" | awk '{print $1}')"
python3 - "$REPORTS/release.json" "$VERSION" "macos-$ARCH" "$DMG_NAME" "$DMG" "$SHA256" <<'PY'
import json
import pathlib
import sys

report, version, platform, filename, dmg, sha256 = sys.argv[1:]
payload = {
    "schema_version": 1,
    "version": version,
    "platform": platform,
    "runner_arch": platform.removeprefix("macos-"),
    "filename": filename,
    "size": pathlib.Path(dmg).stat().st_size,
    "sha256": sha256,
    "unsigned": True,
}
pathlib.Path(report).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY

echo "macOS DMG ready: $DMG"
echo "SHA-256: $SHA256"
