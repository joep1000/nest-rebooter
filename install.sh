#!/usr/bin/env bash
#
# Nest Rebooter — Quick Install Script
#
# Run: curl -sSL <url> | bash   (or just: bash install.sh)
#

set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

INSTALL_DIR="$HOME/.local/share/nest-rebooter"
BIN_LINK="$HOME/.local/bin/nest-rebooter"

echo -e "${CYAN}"
echo "   ╭─────────────────────────────────────╮"
echo "   │     🌙  N E S T   R E B O O T E R  │"
echo "   │     Scheduled WiFi network reboot   │"
echo "   ╰─────────────────────────────────────╯"
echo -e "${NC}"

# ── Check Python ────────────────────────────────────────────────────────────────
echo -e "  ${DIM}Checking Python...${NC}"
if command -v python3 &>/dev/null; then
    PY=$(command -v python3)
    PY_VER=$($PY --version 2>&1 | awk '{print $2}')
    echo -e "  ${GREEN}✓${NC} Python ${PY_VER} at ${PY}"
else
    echo -e "  ${RED}✗${NC} Python 3 not found. Install it first:"
    echo "     sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

# ── Check pip ───────────────────────────────────────────────────────────────────
echo -e "  ${DIM}Checking pip...${NC}"
if ! $PY -m pip --version &>/dev/null; then
    echo -e "  ${YELLOW}⚠${NC} pip not found. Installing..."
    sudo apt-get install -y python3-pip 2>/dev/null || {
        echo -e "  ${RED}✗${NC} Could not install pip. Try: sudo apt install python3-pip"
        exit 1
    }
fi
echo -e "  ${GREEN}✓${NC} pip available"

# ── Create install directory ────────────────────────────────────────────────────
echo -e "  ${DIM}Setting up install directory...${NC}"
mkdir -p "$INSTALL_DIR"
mkdir -p "$(dirname "$BIN_LINK")"

# ── Create virtual environment ──────────────────────────────────────────────────
echo -e "  ${DIM}Creating virtual environment...${NC}"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    $PY -m venv "$INSTALL_DIR/venv"
fi
echo -e "  ${GREEN}✓${NC} Virtual environment at $INSTALL_DIR/venv"

# ── Install dependencies ────────────────────────────────────────────────────────
echo -e "  ${DIM}Installing dependencies...${NC}"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet glocaltokens gpsoauth requests grpcio
echo -e "  ${GREEN}✓${NC} Dependencies installed"

# ── Copy main script ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/nest_rebooter.py" ]; then
    cp "$SCRIPT_DIR/nest_rebooter.py" "$INSTALL_DIR/nest_rebooter.py"
    echo -e "  ${GREEN}✓${NC} Script installed to $INSTALL_DIR/nest_rebooter.py"
else
    echo -e "  ${RED}✗${NC} nest_rebooter.py not found in current directory."
    echo "     Make sure install.sh is in the same directory as nest_rebooter.py"
    exit 1
fi

# ── Create wrapper script ──────────────────────────────────────────────────────
cat > "$BIN_LINK" << WRAPPER
#!/usr/bin/env bash
exec "$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/nest_rebooter.py" "\$@"
WRAPPER
chmod +x "$BIN_LINK"
echo -e "  ${GREEN}✓${NC} Command installed: $BIN_LINK"

# ── Verify ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "  ${GREEN}${BOLD}Installation complete!${NC}"
echo ""
echo -e "  ${BOLD}Quick start:${NC}"
echo ""
echo -e "    ${CYAN}1.${NC} Run setup (need a browser one time for a Google cookie):"
echo -e "       ${BOLD}nest-rebooter setup${NC}"
echo ""
echo -e "    ${CYAN}2.${NC} Test that everything works:"
echo -e "       ${BOLD}nest-rebooter test${NC}"
echo ""
echo -e "    ${CYAN}3.${NC} Install the 3 AM daily timer:"
echo -e "       ${BOLD}nest-rebooter install${NC}"
echo ""
echo -e "  If ${BOLD}nest-rebooter${NC} is not found, add this to your ~/.bashrc:"
echo -e "    ${DIM}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
echo ""
