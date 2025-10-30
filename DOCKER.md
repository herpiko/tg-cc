# Docker Setup Guide for tg-cc

This guide provides detailed instructions for running the tg-cc Telegram bot using Docker.

## Prerequisites

Before you begin, ensure you have:

1. **Docker** installed (version 20.10 or higher)
2. **Docker Compose** installed (version 1.29 or higher)
3. **Telegram Bot Token** from @BotFather
4. **Claude Code CLI** credentials configured on your host
5. **SSH keys** for Git repository access
6. **GitLab CLI (glab)** authentication configured (if using GitLab)

## Quick Start

### Using Make (Recommended)

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url>
   cd tg-cc
   ```

2. **Initial setup:**
   ```bash
   make setup
   ```
   This creates `.env` from the example file.

3. **Edit `.env` file:**
   ```bash
   nano .env
   ```
   Set the following variables:
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   SSH_KEY_PATH=/home/user/.ssh
   CLAUDE_CONFIG_PATH=/home/user/.config/claude
   GIT_CONFIG_PATH=/home/user/.gitconfig
   ```

4. **Configure projects:**
   Edit `config.yaml` to set up:
   - Authorized Telegram users
   - Authorized group chat IDs
   - Projects configuration
   - Command rules

5. **Build and run:**
   ```bash
   make run
   ```

6. **Check logs:**
   ```bash
   make logs
   ```

### Using Docker Compose Directly

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url>
   cd tg-cc
   ```

2. **Create environment file:**
   ```bash
   cp .env.example .env
   ```

3. **Edit `.env` file:**
   ```bash
   nano .env
   ```
   Set the following variables:
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   SSH_KEY_PATH=/home/user/.ssh
   CLAUDE_CONFIG_PATH=/home/user/.config/claude
   GIT_CONFIG_PATH=/home/user/.gitconfig
   ```

4. **Configure projects:**
   Edit `config.yaml` to set up:
   - Authorized Telegram users
   - Authorized group chat IDs
   - Projects configuration
   - Command rules

5. **Build and run:**
   ```bash
   docker-compose up -d
   ```

6. **Check logs:**
   ```bash
   docker-compose logs -f tg-cc
   ```

## Directory Structure

```
tg-cc/
├── Dockerfile              # Docker image definition
├── docker-compose.yml      # Docker Compose configuration
├── .env                    # Environment variables (create from .env.example)
├── .env.example            # Example environment file
├── config.yaml             # Bot configuration
├── tg-cc                   # Main bot script
├── requirements.txt        # Python dependencies
└── DOCKER.md              # This file
```

## Volume Mounts

The Docker container requires several volume mounts for proper operation:

### 1. Configuration File (config.yaml)
```yaml
- ./config.yaml:/app/config.yaml:ro
```
- **Purpose:** Bot configuration (users, groups, projects, rules)
- **Permissions:** Read-only
- **Required:** Yes

### 2. SSH Keys
```yaml
- ${SSH_KEY_PATH}:/root/.ssh:ro
```
- **Purpose:** Git authentication for cloning repositories
- **Permissions:** Read-only
- **Required:** Yes (if using SSH URLs for git)
- **Default:** `~/.ssh`

### 3. Claude Configuration
```yaml
- ${CLAUDE_CONFIG_PATH}:/root/.config/claude:ro
```
- **Purpose:** Claude Code CLI authentication
- **Permissions:** Read-only
- **Required:** Yes
- **Default:** `~/.config/claude`

### 4. Git Configuration
```yaml
- ${GIT_CONFIG_PATH}:/root/.gitconfig:ro
```
- **Purpose:** Git user configuration
- **Permissions:** Read-only
- **Required:** Optional
- **Default:** `~/.gitconfig`

### 5. Workspace
```yaml
- workspace:/workspace
```
- **Purpose:** Persistent storage for cloned repositories
- **Type:** Docker volume
- **Required:** Yes

### 6. Temporary Files
```yaml
- /tmp/tg-cc:/tmp
```
- **Purpose:** Output files from Claude commands
- **Type:** Bind mount
- **Required:** Yes

## Building the Image

### Build locally:
```bash
docker build -t herpiko/tg-cc:latest .
```

### Build with specific tag:
```bash
docker build -t herpiko/tg-cc:v1.0.0 .
```

### Build with no cache:
```bash
docker build --no-cache -t herpiko/tg-cc:latest .
```

## Pushing to Docker Hub

1. **Login to Docker Hub:**
   ```bash
   docker login
   ```

2. **Build the image:**
   ```bash
   docker build -t herpiko/tg-cc:latest .
   ```

3. **Tag the image (if needed):**
   ```bash
   docker tag herpiko/tg-cc:latest herpiko/tg-cc:v1.0.0
   ```

4. **Push to Docker Hub:**
   ```bash
   docker push herpiko/tg-cc:latest
   docker push herpiko/tg-cc:v1.0.0
   ```

## Makefile Commands

The project includes a Makefile with convenient shortcuts for common Docker operations:

| Command | Description |
|---------|-------------|
| `make help` | Show all available commands |
| `make setup` | Initial setup (create .env from example) |
| `make build` | Build the Docker image |
| `make build-no-cache` | Build without cache |
| `make push` | Push image to Docker Hub |
| `make pull` | Pull image from Docker Hub |
| `make run` | Start the bot (detached mode) |
| `make run-foreground` | Start the bot in foreground |
| `make stop` | Stop the bot |
| `make restart` | Restart the bot |
| `make down` | Stop and remove container |
| `make logs` | Show logs (follow mode) |
| `make logs-tail` | Show last 100 lines of logs |
| `make status` | Show container status |
| `make exec` | Execute shell in container |
| `make clean` | Remove containers and volumes |
| `make prune` | Remove unused Docker resources |
| `make prune-all` | Remove all unused resources including images |
| `make backup` | Backup workspace volume |
| `make restore` | Restore workspace (use BACKUP_FILE=filename) |
| `make update` | Pull latest image and restart |
| `make rebuild` | Rebuild and restart |
| `make config-test` | Test configuration files |
| `make stats` | Show container resource usage |
| `make version` | Show Docker versions |
| `make all` | Build, push, and run |

**Examples:**
```bash
# Initial setup
make setup

# Build and run
make build
make run

# View logs
make logs

# Backup workspace
make backup

# Restore from backup
make restore BACKUP_FILE=workspace-backup-20241030-120000.tar.gz

# Update to latest version
make update
```

## Running the Container

### Using Docker Compose (Recommended)

**Start the bot:**
```bash
docker-compose up -d
```

**View logs:**
```bash
docker-compose logs -f
```

**Stop the bot:**
```bash
docker-compose stop
```

**Restart the bot:**
```bash
docker-compose restart
```

**Remove the container:**
```bash
docker-compose down
```

**Remove container and volumes:**
```bash
docker-compose down -v
```

### Using Docker CLI

**Run the container:**
```bash
docker run -d \
  --name tg-cc-bot \
  --restart unless-stopped \
  -e TELEGRAM_BOT_TOKEN="your_token_here" \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v ~/.ssh:/root/.ssh:ro \
  -v ~/.config/claude:/root/.config/claude:ro \
  -v ~/.gitconfig:/root/.gitconfig:ro \
  -v tg-cc-workspace:/workspace \
  -v /tmp/tg-cc:/tmp \
  herpiko/tg-cc:latest \
  --api-token YOUR_TELEGRAM_BOT_TOKEN
```

## Configuration

### config.yaml

```yaml
authorized_users:
  - "your_telegram_username"

authorized_groups:
  - "-1234567890"  # Your group chat ID (negative for groups)

ask_rules: |
  - Do not reply with too long message.

feat_rules: |
  - Always create a new branch for features
  - Use feat- prefix for branch names
  - Create merge request to main branch

fix_rules: |
  - Always create a new branch for fixes
  - Use fix- prefix for branch names
  - Create merge request to main branch

feedback_rules: |
  - Continue work on existing branch
  - Do not switch branches

projects:
  - project_name: "example-project"
    project_repo: "git@gitlab.com:user/repo.git"
    project_workdir: "/workspace/example-project"
```

**Important Notes:**
- Use `/workspace/` prefix for `project_workdir` to ensure persistence
- Group chat IDs are negative numbers
- SSH URLs require proper SSH key configuration

## Troubleshooting

### Container won't start
**Check logs:**
```bash
docker-compose logs tg-cc
```

**Common issues:**
- Missing environment variables in `.env`
- Invalid Telegram bot token
- Missing or incorrect volume paths

### Bot doesn't respond
**Verify authorization:**
- Check your Telegram username is in `authorized_users`
- Verify group chat ID is in `authorized_groups`
- Ensure Privacy Mode is disabled in @BotFather

**Check bot logs:**
```bash
docker-compose logs -f tg-cc
```

### Git clone fails
**SSH key issues:**
- Ensure SSH keys are mounted correctly
- Verify SSH keys have correct permissions (600 for private key)
- Test SSH connection: `docker-compose exec tg-cc ssh -T git@gitlab.com`

**Fix SSH permissions:**
```bash
chmod 600 ~/.ssh/id_rsa
chmod 644 ~/.ssh/id_rsa.pub
```

### Claude Code CLI not working
**Check Claude configuration:**
```bash
docker-compose exec tg-cc claude --version
```

**Verify Claude config is mounted:**
```bash
docker-compose exec tg-cc ls -la /root/.config/claude
```

### Out of disk space
**Check Docker disk usage:**
```bash
docker system df
```

**Clean up old images and containers:**
```bash
docker system prune -a
```

**Check workspace volume size:**
```bash
docker volume ls
docker volume inspect tg-cc_workspace
```

## Security Considerations

1. **Isolated Environment:**
   - Run the container in an isolated network
   - Do not expose any ports
   - Use read-only mounts where possible

2. **Credentials:**
   - Never commit `.env` file to version control
   - Use Docker secrets for production deployments
   - Rotate Telegram bot token regularly

3. **Resource Limits:**
   - Configure CPU and memory limits in `docker-compose.yml`
   - Monitor resource usage regularly

4. **Updates:**
   - Keep Docker images updated
   - Rebuild images when dependencies change
   - Monitor security advisories

## Monitoring

### View real-time logs:
```bash
docker-compose logs -f tg-cc
```

### Check container status:
```bash
docker-compose ps
```

### Inspect container:
```bash
docker-compose exec tg-cc /bin/bash
```

### Monitor resource usage:
```bash
docker stats tg-cc-bot
```

## Backup and Recovery

### Backup workspace volume:
```bash
docker run --rm \
  -v tg-cc_workspace:/workspace \
  -v $(pwd):/backup \
  alpine tar czf /backup/workspace-backup.tar.gz -C /workspace .
```

### Restore workspace volume:
```bash
docker run --rm \
  -v tg-cc_workspace:/workspace \
  -v $(pwd):/backup \
  alpine tar xzf /backup/workspace-backup.tar.gz -C /workspace
```

### Backup configuration:
```bash
cp config.yaml config.yaml.backup
```

## Maintenance

### Update the image:
```bash
docker-compose pull
docker-compose up -d
```

### Rebuild after code changes:
```bash
docker-compose build --no-cache
docker-compose up -d
```

### Clean up old images:
```bash
docker image prune -a
```

## Support

For issues and questions:
- Check the logs first: `docker-compose logs -f`
- Review this documentation
- Check the main README.md for general bot usage
- Verify all prerequisites are met

## License

MIT - See LICENSE file for details
