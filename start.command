#!/usr/bin/env bash
# =========================================================
#  Move to script directory
# =========================================================
cd "$(dirname "$0")" || { echo "[ERROR] Failed to switch to the script directory."; exit 1; }

# =========================================================
#  Config
# =========================================================
APP_FILE="app.py"
REQUIREMENTS_FILE="requirements.txt"
VENV_DIR=".venv"
PORT=5001
PIP_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
PY_CMD=""

# =========================================================
#  Detect Python
# =========================================================
echo "[INFO] Detecting Python..."

for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        if [ "$version" = "3" ]; then
            PY_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PY_CMD" ]; then
    echo "[ERROR] Python 3.8+ is required."
    echo "[TIP]   Please install Python from: https://www.python.org/downloads/"
    open "https://www.python.org/downloads/"
    exit 1
fi

echo "[INFO] Python command: $PY_CMD"
"$PY_CMD" --version
if [ $? -ne 0 ]; then
    echo "[ERROR] Python is detected but cannot run."
    exit 1
fi

# =========================================================
#  Create venv if missing
# =========================================================
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "[INFO] Creating virtual environment..."
    "$PY_CMD" -m venv "$VENV_DIR" || { echo "[ERROR] Failed to create virtual environment."; exit 1; }
fi

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "[ERROR] Virtual environment activation file missing."
    exit 1
fi

source "$VENV_DIR/bin/activate" || { echo "[ERROR] Failed to activate virtual environment."; exit 1; }
PY_CMD="python"

# =========================================================
#  Upgrade pip if needed
# =========================================================
if ! "$PY_CMD" -m pip --version &>/dev/null; then
    echo "[INFO] Bootstrapping pip..."
    "$PY_CMD" -m ensurepip --upgrade || { echo "[ERROR] pip is not available and cannot be bootstrapped."; exit 1; }
fi

# =========================================================
#  Install dependencies
# =========================================================
echo "[INFO] Checking and installing dependencies..."

if [ -f "$REQUIREMENTS_FILE" ]; then
    "$PY_CMD" -m pip install --upgrade pip
    [ $? -ne 0 ] && echo "[WARN] pip upgrade failed, continuing..."

    "$PY_CMD" -m pip install -r "$REQUIREMENTS_FILE"
    if [ $? -ne 0 ]; then
        echo "[WARN] Retry installing dependencies with mirror..."
        "$PY_CMD" -m pip install -r "$REQUIREMENTS_FILE" -i "$PIP_MIRROR"
        [ $? -ne 0 ] && { echo "[ERROR] Failed to install dependencies from requirements.txt."; exit 1; }
    fi
else
    echo "[WARN] requirements.txt not found. No dependency install performed."
fi

# =========================================================
#  Check app file
# =========================================================
if [ ! -f "$APP_FILE" ]; then
    echo "[ERROR] Entry file not found: $APP_FILE"
    exit 1
fi

# =========================================================
#  Check port
# =========================================================
if lsof -iTCP:"$PORT" -sTCP:LISTEN &>/dev/null; then
    PID=$(lsof -ti TCP:"$PORT" -sTCP:LISTEN)
    echo "[ERROR] Port $PORT is already in use. PID=$PID"
    echo "[TIP]   Use: ps -p $PID to inspect the process"
    exit 1
fi

# =========================================================
#  Start app
# =========================================================
echo "[INFO] Starting application..."
echo "[INFO] Open: http://127.0.0.1:$PORT"
"$PY_CMD" "$APP_FILE"
RET=$?

[ $RET -ne 0 ] && echo "[ERROR] Application exited with code $RET"

read -rp "Press Enter to exit..."
exit $RET