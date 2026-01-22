#!/bin/bash

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== tgcc Systemd Service Installer ===${NC}"
echo

# Check if running as root or can use sudo
if [ "$EUID" -ne 0 ]; then
    if ! command -v sudo &> /dev/null; then
        echo -e "${RED}Error: This script requires root privileges. Please run as root or install sudo.${NC}"
        exit 1
    fi
    SUDO="sudo"
else
    SUDO=""
fi

# Ask for service name suffix
echo -e "${YELLOW}1. Enter the project/service name (will be prefixed with 'tgcc-'):${NC}"
read -p "   Service name suffix: " SERVICE_SUFFIX

if [ -z "$SERVICE_SUFFIX" ]; then
    echo -e "${RED}Error: Service name cannot be empty.${NC}"
    exit 1
fi

# Sanitize service name (only allow alphanumeric and hyphens)
SERVICE_SUFFIX=$(echo "$SERVICE_SUFFIX" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')
SERVICE_NAME="tgcc-${SERVICE_SUFFIX}"

echo -e "   Service will be named: ${GREEN}${SERVICE_NAME}${NC}"
echo

# Ask for config file path
echo -e "${YELLOW}2. Enter the full path to the config file:${NC}"
read -p "   Config file path: " CONFIG_PATH

if [ -z "$CONFIG_PATH" ]; then
    echo -e "${RED}Error: Config file path cannot be empty.${NC}"
    exit 1
fi

# Expand tilde if present
CONFIG_PATH="${CONFIG_PATH/#\~/$HOME}"

# Convert to absolute path
CONFIG_PATH=$(realpath "$CONFIG_PATH" 2>/dev/null || echo "$CONFIG_PATH")

if [ ! -f "$CONFIG_PATH" ]; then
    echo -e "${RED}Error: Config file not found at: ${CONFIG_PATH}${NC}"
    exit 1
fi

echo -e "   Using config file: ${GREEN}${CONFIG_PATH}${NC}"
echo

# Determine the working directory (directory containing the config file)
WORKING_DIR=$(dirname "$CONFIG_PATH")

# Determine the user to run the service as
CURRENT_USER=$(whoami)
if [ "$CURRENT_USER" = "root" ]; then
    echo -e "${YELLOW}3. Enter the username to run the service as:${NC}"
    read -p "   Username: " RUN_USER
    if [ -z "$RUN_USER" ]; then
        echo -e "${RED}Error: Username cannot be empty when running as root.${NC}"
        exit 1
    fi
else
    RUN_USER="$CURRENT_USER"
    echo -e "${YELLOW}3. Service will run as user: ${GREEN}${RUN_USER}${NC}"
fi

# Get the user's home directory
USER_HOME=$(eval echo "~$RUN_USER")

# Find tgcc executable
TG_CC_PATH=$(which tgcc 2>/dev/null || echo "")

if [ -z "$TG_CC_PATH" ]; then
    # Try common locations
    if [ -f "$USER_HOME/.local/bin/tgcc" ]; then
        TG_CC_PATH="$USER_HOME/.local/bin/tgcc"
    elif [ -f "/usr/local/bin/tgcc" ]; then
        TG_CC_PATH="/usr/local/bin/tgcc"
    else
        echo -e "${YELLOW}   Could not find tgcc in PATH. Enter the full path to tgcc:${NC}"
        read -p "   tgcc path: " TG_CC_PATH
        TG_CC_PATH="${TG_CC_PATH/#\~/$USER_HOME}"
        if [ ! -f "$TG_CC_PATH" ]; then
            echo -e "${RED}Error: tgcc not found at: ${TG_CC_PATH}${NC}"
            exit 1
        fi
    fi
fi

echo -e "   Using tgcc at: ${GREEN}${TG_CC_PATH}${NC}"
echo

# Create the systemd service file
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
TEMP_SERVICE_FILE=$(mktemp)

cat > "$TEMP_SERVICE_FILE" << EOF
[Unit]
Description=tgcc Telegram Bot - ${SERVICE_SUFFIX}
After=network.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${WORKING_DIR}
ExecStart=${TG_CC_PATH} -c ${CONFIG_PATH}
Restart=on-failure
RestartSec=10

# Environment variables (uncomment and modify as needed)
# Environment=TELEGRAM_BOT_TOKEN=your_token_here
# Environment=SSH_KEY_PATH=${USER_HOME}/.ssh
# Environment=CLAUDE_CONFIG_PATH=${USER_HOME}/.config/claude

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${WORKING_DIR}
ReadWritePaths=/tmp

[Install]
WantedBy=multi-user.target
EOF

echo -e "${YELLOW}4. Installing systemd service...${NC}"

# Copy service file to systemd directory
$SUDO cp "$TEMP_SERVICE_FILE" "$SERVICE_FILE"
$SUDO chmod 644 "$SERVICE_FILE"
rm "$TEMP_SERVICE_FILE"

echo -e "   Service file created at: ${GREEN}${SERVICE_FILE}${NC}"

# Reload systemd daemon
echo -e "${YELLOW}5. Reloading systemd daemon...${NC}"
$SUDO systemctl daemon-reload

# Enable the service
echo -e "${YELLOW}6. Enabling service...${NC}"
$SUDO systemctl enable "$SERVICE_NAME"

# Start the service
echo -e "${YELLOW}7. Starting service...${NC}"
$SUDO systemctl start "$SERVICE_NAME"

# Check status
echo
echo -e "${GREEN}=== Installation Complete ===${NC}"
echo
echo -e "Service status:"
$SUDO systemctl status "$SERVICE_NAME" --no-pager || true

echo
echo -e "${GREEN}Useful commands:${NC}"
echo -e "  Check status:  ${YELLOW}sudo systemctl status ${SERVICE_NAME}${NC}"
echo -e "  View logs:     ${YELLOW}sudo journalctl -u ${SERVICE_NAME} -f${NC}"
echo -e "  Stop service:  ${YELLOW}sudo systemctl stop ${SERVICE_NAME}${NC}"
echo -e "  Start service: ${YELLOW}sudo systemctl start ${SERVICE_NAME}${NC}"
echo -e "  Restart:       ${YELLOW}sudo systemctl restart ${SERVICE_NAME}${NC}"
echo -e "  Disable:       ${YELLOW}sudo systemctl disable ${SERVICE_NAME}${NC}"
