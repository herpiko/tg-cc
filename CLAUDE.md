# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a chat bot (`ccc`) that integrates Claude AI to perform software development tasks through chat commands. The bot supports both **Telegram** and **Lark** (Feishu) platforms. It uses the Claude Code Agent SDK (`claude-agent-sdk`) with `bypassPermissions` mode to perform complex development operations on configured projects.

**Security Notice**: This bot MUST run in an isolated environment (VM, container, or dedicated machine). It has full file system access and executes arbitrary commands via Claude.

## Running the Bot

Start the bot:
```bash
# Run both Telegram and Lark bots (if configured)
ccc -c config.yaml

# Run Telegram bot only
ccc -c config.yaml --telegram

# Run Lark bot only
ccc -c config.yaml --lark

# Legacy command (still works)
tgcc -c config.yaml
```

The bot requires:
- Python 3.10+
- Claude Code CLI installed and configured
- Git (for repository cloning)
- Network access and credentials (Claude Code, SSH for git, `glab` for GitLab)

## Configuration

All configuration is in `config.yaml`:

```yaml
# Shared configuration
authorized_users: ["username"]       # Usernames (shared across platforms)
ask_rules: |                         # System prompt for /ask
feat_rules: |                        # System prompt for /feat
fix_rules: |                         # System prompt for /fix
feedback_rules: |                    # System prompt for /feedback

# Telegram configuration
telegram:
  bot_token: "your_telegram_bot_token"
  authorized_groups:
    - group: "-1234567890"
      sub: "12345"  # Optional: thread_id for topic/subgroup

# Lark configuration
lark:
  app_id: "cli_xxx"
  app_secret: "xxx"
  verification_token: "xxx"
  encrypt_key: ""  # Optional, for encrypted events
  webhook_port: 8080
  authorized_users: ["ou_xxx"]  # Lark user open_ids
  authorized_chats: ["oc_xxx"]  # Lark chat_ids

# Project configuration
projects:
  - project_name: "name"
    project_repo: "git@gitlab.com:user/repo.git"
    project_workdir: "/path/to/workdir"
    project_up: "make run"      # Optional: command to start project
    project_reset: "make purge" # Optional: command to reset project
```

**Authorization**:
- Telegram: Requires BOTH user in `authorized_users` AND chat in `telegram.authorized_groups`
- Lark: Requires BOTH user in `lark.authorized_users` AND chat in `lark.authorized_chats`

**Telegram Privacy Mode**: Must be disabled in @BotFather for group functionality:
1. Send `/mybots` to @BotFather
2. Select bot → Bot Settings → Group Privacy → Turn off
3. Remove and re-add bot to group

## Architecture

### File Structure

```
ccc/
├── __init__.py           # Package init, version
├── __main__.py           # Unified entry point with --telegram/--lark flags
├── config.py             # Unified configuration loader
├── claude.py             # Claude SDK operations (platform-agnostic)
├── git.py                # Git operations (platform-agnostic)
├── process.py            # Process management (platform-agnostic)
├── messenger.py          # Abstract messenger interface
├── telegram/             # Telegram-specific implementation
│   ├── __init__.py
│   ├── bot.py            # Telegram application setup
│   ├── handlers.py       # Telegram command handlers
│   └── messenger.py      # TelegramMessenger implementation
└── lark/                 # Lark-specific implementation
    ├── __init__.py
    ├── bot.py            # Flask webhook server
    ├── handlers.py       # Lark command handlers
    └── messenger.py      # LarkMessenger implementation
```

### Core Components

1. **Messenger Abstraction** (`messenger.py`):
   - Abstract `Messenger` class defining `reply()` and `get_thread_context()` methods
   - Platform-specific implementations in `telegram/messenger.py` and `lark/messenger.py`

2. **Configuration Loading** (`config.py`):
   - Loads config.yaml at startup
   - Manages shared config (projects, rules) and platform-specific config (Telegram tokens, Lark credentials)

3. **Core Operations** (platform-agnostic):
   - `claude.py`: Claude SDK query execution, session management
   - `git.py`: Repository cloning, branch management
   - `process.py`: Project spin-up/shutdown, log management

4. **Platform Implementations**:
   - `telegram/`: python-telegram-bot based implementation
   - `lark/`: Flask webhook server with lark-oapi SDK

### Command Flow (feat/fix/feedback)

1. Authorization check (platform-specific)
2. Parse project name and user prompt
3. Find project in PROJECTS list
4. Clone repository if needed
5. Initialize CLAUDE.md if needed
6. Generate UUID for this request
7. Create `/tmp/output_{uuid}.txt` path
8. Build prompt with project info and output file path
9. Execute Claude query via SDK
10. Read output file and send to user via messenger
11. Clean up output file

### Output File Management

- Each request generates UUID to prevent conflicts
- Output files: `/tmp/output_{uuid}.txt`
- Bot instructs Claude to write results to this file
- After completion, execution time is appended
- File is sent to user (truncated at 4000 chars)
- File is deleted after sending

## Command Specifics

### /help
- Show available commands

### /ask
**Format**: `/ask project-name query`
- Ask a question about a project
- Uses `ask_rules` system prompt

### /feat
**Format**: `/feat project-name task`
- Creates new branch (feat-* prefix)
- Implements features
- Creates merge request to `main` branch
- Uses `feat_rules` system prompt

### /fix
**Format**: `/fix project-name issue`
- Creates new branch (fix-* prefix)
- Fixes bugs
- Creates merge request to `main` branch
- Uses `fix_rules` system prompt

### /plan
**Format**: `/plan project-name task`
- Creates new branch (plan-* prefix)
- For planning and exploration tasks
- Uses `plan_rules` system prompt

### /feedback
**Format**: `/feedback project-name feedback`
- Continues work on existing branch (does NOT switch branches)
- Maintains session context from previous /feat, /fix, or /plan
- Uses `feedback_rules` system prompt

### /init
**Format**: `/init project-name`
- Initializes CLAUDE.md for a project
- Clones repository if needed
- Commits and pushes CLAUDE.md to main branch
- Spins up project if `project_up` is configured

### /up
**Format**: `/up project-name`
- Spin up a project using `project_up` command

### /stop
**Format**: `/stop project-name`
- Stop a running project

### /status
- Show running projects

### /cancel
**Format**: `/cancel [project-name]`
- Cancel running Claude query
- Cancels all if no project specified

### /log
**Format**: `/log project-name [lines]`
- Show last N lines of project logs (default: 50)

### /cost
- Display Claude API usage costs via claude-monitor

### /selfupdate
- Update bot from GitHub and restart (Telegram only)

## Key Behaviors

1. **Branch Management**: feat/fix/plan commands create new branches with appropriate prefixes. feedback continues existing branch.

2. **GitLab Integration**: Uses `glab` CLI for merge request creation.

3. **No Empty MRs**: If no code changes, don't create merge request.

4. **Output Format**: Commands should write summary to output file including merge request link.

5. **Target Branch**: Default is `main` unless specified otherwise in task.

6. **Codebase Initialization**: If CLAUDE.md doesn't exist in project, runs `claude /init` automatically before executing task.

7. **Lark Thread Handling**: Replies are sent to the same thread as the original command.

## Error Handling

- Authorization failures: Reply with unauthorized message
- Missing project: Show available projects
- Git clone failure: Show error and abort
- CLAUDE.md initialization failure: Show error and abort
- SDK query errors: Log and notify user
- Missing output file: Error message to user

## Dependencies

Core:
- `python-telegram-bot>=22.0`
- `PyYAML>=6.0`
- `claude-agent-sdk>=0.1.0`

Lark support:
- `lark-oapi>=1.0.0`
- `flask>=3.0.0`
- `pycryptodome>=3.0.0` (for encrypted events)
