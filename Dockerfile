FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    openssh-client \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install glab CLI for GitLab
RUN curl -s https://gitlab.com/gitlab-org/cli/-/releases/permalink/latest/downloads/glab_linux_amd64.tar.gz | \
    tar -xz -C /usr/local/bin glab

# Install Claude Code CLI
# Note: Adjust the installation command based on actual Claude Code CLI installation method
# This assumes the CLI is available via a standard installer
RUN curl -fsSL https://deb.anthropic.com/claude-installer.sh | sh || \
    echo "Warning: Claude Code CLI installation failed. Please install manually."

# Add Claude CLI to PATH
ENV PATH="/root/.local/bin:${PATH}"

# Install claude-monitor
# Note: Adjust based on actual installation method (pip/npm/etc)
RUN pip install --no-cache-dir anthropic-monitor || \
    pip install --no-cache-dir claude-monitor || \
    echo "Warning: claude-monitor installation skipped. Install manually if needed."

# Create app directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot script
COPY tg-cc .
RUN chmod +x tg-cc

# Create necessary directories
RUN mkdir -p /root/.ssh \
    && mkdir -p /root/.config/claude \
    && mkdir -p /tmp \
    && mkdir -p /workspace

# Set up SSH config for Git operations
RUN echo "Host *\n\
    StrictHostKeyChecking no\n\
    UserKnownHostsFile=/dev/null" > /root/.ssh/config

# Expose no ports (bot uses Telegram API, no incoming connections needed)

# Set the entrypoint
ENTRYPOINT ["./tg-cc"]
