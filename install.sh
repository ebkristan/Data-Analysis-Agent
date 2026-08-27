#!/usr/bin/env sh
set -eu

REPO_URL="https://github.com/Zafer-Liu/Data-Analysis-Agent.git"
PROJECT_NAME="Data-Analysis-Agent"
INSTALL_DIR="$HOME/.data-analysis-agent"
PROJECT_DIR="$INSTALL_DIR/$PROJECT_NAME"
LAUNCHER="$HOME/.local/bin/data-analysis-agent"

info() {
  printf '[Data-Analysis-Agent] %s\n' "$1"
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python3 not found. Please install Python 3.10+ first." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Git not found. Please install Git first." >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"
mkdir -p "$HOME/.local/bin"

if [ -d "$PROJECT_DIR" ]; then
  info "Project already exists. Updating..."
  cd "$PROJECT_DIR"
  git pull
else
  info "Cloning project..."
  git clone "$REPO_URL" "$PROJECT_DIR"
  cd "$PROJECT_DIR"
fi

info "Creating virtual environment..."
python3 -m venv .venv

info "Installing dependencies..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env sh
cd "$PROJECT_DIR"
. ".venv/bin/activate"
python app.py
EOF

chmod +x "$LAUNCHER"

info "Installed successfully."
info "Start with: data-analysis-agent"
info "If command not found, add this to your shell config:"
info 'export PATH="$HOME/.local/bin:$PATH"'