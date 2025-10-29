# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Telegram bot (`tg-cc`) that integrates Claude AI to perform software development tasks through chat commands. The bot executes Claude Code CLI with `--dangerously-skip-permissions` flag to perform complex development operations on configured projects.

**Security Notice**: This bot MUST run in an isolated environment (VM, container, or dedicated machine). It has full file system access and executes arbitrary commands via Claude.

## Running the Bot

Start the bot:
```bash
./tg-cc --api-token YOUR_TELEGRAM_BOT_TOKEN
```

The bot requires:
- Python 3.8+
- Claude Code CLI installed and configured
- Git (for repository cloning)
- Network access and credentials (Claude Code, SSH for git, `glab` for GitLab)

## Configuration

All configuration is in `projects.yaml` (or `config.yaml`):

```yaml
authorized_users: ["username"]       # Telegram usernames
authorized_groups: ["-1234567890"]   # Chat IDs (negative for groups)
ask_rules: |                         # System prompt for /ask
feat_rules: |                        # System prompt for /feat
fix_rules: |                         # System prompt for /fix
feedback_rules: |                    # System prompt for /feedback
projects:
  - project_name: "name"
    project_repo: "git@gitlab.com:user/repo.git"
    project_workdir: "/path/to/workdir"
```

**Authorization**: Bot requires BOTH user in `authorized_users` AND chat in `authorized_groups`.

**Telegram Privacy Mode**: Must be disabled in @BotFather for group functionality:
1. Send `/mybots` to @BotFather
2. Select bot → Bot Settings → Group Privacy → Turn off
3. Remove and re-add bot to group

## Architecture

### Core Components

1. **Configuration Loading** (`load_config()`):
   - Loads projects.yaml at startup
   - Populates global variables: PROJECTS, AUTHORIZED_USERS, AUTHORIZED_GROUPS, and rule sets

2. **Authorization** (`is_authorized()`):
   - Dual authorization: username AND chat_id must both be in allowed lists
   - Returns False if either check fails

3. **Helper Functions**:
   - `run_claude_command(cmd, cwd, request_uuid)`: Executes Claude with timing tracking
   - `process_output_file(update, output_file, duration_minutes)`: Reads output file, sends to user, appends execution time, cleans up
   - `cleanup_output_file(output_file)`: Cleanup utility for error cases
   - `clone_repository_if_needed(update, project_repo, project_workdir)`: Clones repository if directory doesn't exist
   - `initialize_claude_md_if_needed(update, project_workdir)`: Checks for CLAUDE.md, runs `claude /init` if missing

4. **Command Handlers**:
   - `/ask`: General questions without project context
   - `/feat`: Implement features in a project
   - `/fix`: Fix bugs in a project
   - `/feedback`: Continue work on existing branch
   - `/init`: Initialize CLAUDE.md for a project

### Command Flow (feat/fix/feedback)

1. Authorization check
2. Parse project name and user prompt
3. Find project in PROJECTS list
4. Clone repository if needed (`clone_repository_if_needed`)
5. Initialize CLAUDE.md if needed (`initialize_claude_md_if_needed`)
6. Generate UUID for this request
7. Create `/tmp/output_{uuid}.txt` path
8. Build prompt with project info and output file path
9. Execute Claude CLI with:
   - `--dangerously-skip-permissions`
   - `--verbose`
   - `--system-prompt` with appropriate rules
   - Working directory set to project_workdir
10. Read output file and send to user
11. Clean up output file

### Output File Management

- Each request generates UUID to prevent conflicts
- Output files: `/tmp/output_{uuid}.txt`
- Bot instructs Claude to write results to this file
- After completion, execution time is appended
- File is sent to user (truncated at 4000 chars for Telegram limits)
- File is deleted after sending

### Subprocess Execution

- All Claude commands use 30-minute timeout (1800 seconds)
- Commands executed via `subprocess.run()` with list arguments (handles spaces/newlines automatically)
- Git operations also have 30-minute timeout

## Command Specifics

### /ask
**Format**: `/ask query`
- Runs in bot's directory (not project-specific)
- Uses `ask_rules` system prompt
- For general questions and quick tasks

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

### /feedback
**Format**: `/feedback project-name feedback`
- Continues work on existing branch (does NOT switch branches)
- For iterative development
- Uses `feedback_rules` system prompt

### /init
**Format**: `/init project-name`
- Manually initializes CLAUDE.md for a project
- Clones repository if needed
- Runs `initialize_claude_md_if_needed()` to generate CLAUDE.md
- Commits and pushes CLAUDE.md to main branch
- No other work is performed (useful for setting up new projects)

## Key Behaviors

1. **Branch Management**: feat/fix commands always create new branches with appropriate prefixes. feedback continues existing branch.

2. **GitLab Integration**: Uses `glab` CLI for merge request creation.

3. **No Empty MRs**: If no code changes, don't create merge request.

4. **Output Format**: Commands should write summary to output file including merge request link.

5. **Target Branch**: Default is `main` unless specified otherwise in task.

6. **Codebase Initialization**: If CLAUDE.md doesn't exist in project, runs `claude /init` automatically before executing task.

## File Structure

- `tg-cc`: Main executable (Python script with shebang)
- `projects.yaml` or `config.yaml`: Configuration file
- `requirements.txt`: Python dependencies
- `README.md`: Documentation with security warnings
- `/tmp/output_*.txt`: Temporary output files (auto-cleaned)

## Error Handling

- Authorization failures: Reply with authorized user list
- Missing project: Show available projects
- Git clone failure: Show error and abort
- CLAUDE.md initialization failure: Show error and abort
- Command timeout: 30-minute limit
- Subprocess errors: Log and notify user
- Missing output file: Error message to user
