# tg-ccc

A Telegram bot that integrates Claude AI to help with software development tasks through chat commands. This bot can execute Claude Code with dangerous mode enabled to perform complex development tasks like implementing features, fixing bugs, and more. Image having a software development team but they are not human. Give them instructions then review their work before merge their changes.

This project was fully written by Claude Code with `--dangerously-skip-permissions` mode enabled. The project runs with the same mode.

## Important Security Notice

**ALWAYS run this bot in an isolated environment such as:**
- A dedicated VM
- A Docker container
- A separate development machine
- A sandboxed environment

**DO NOT run this on:**
- Your primary development machine
- Production servers
- Machines with sensitive data
- Shared systems

The bot executes Claude commands with dangerous mode, which allows it to make arbitrary file system changes, run commands, and potentially perform destructive operations.

## Development Status

This project is subject to changes as it is still in **active and heavy development**. Features, commands, and behavior may change without notice.

## Features

- **Multiple Commands**: `/ask`, `/feat`, `/feedback`, `/fix`
- **Project Management**: Configure multiple projects via YAML
- **Authorization**: User and group-based access control
- **Execution Tracking**: Automatic timing and logging
- **Git Integration**: Automatic repository cloning
- **System Prompts**: Configurable rules per command type

## Prerequisites

- Python 3.8+
- Claude Code CLI installed and configured
- Telegram Bot API token (from @BotFather)
- Git (for repository cloning)

## Installation

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url>
   cd tg-claude
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Make the script executable:**
   ```bash
   chmod +x tg-claude
   ```

4. **Configure projects.yaml:**
   Edit `projects.yaml` to set up:
   - Authorized users
   - Authorized groups
   - Projects
   - Command rules

## Configuration

### projects.yaml Structure

```yaml
authorized_users:
  - "your_telegram_username"

authorized_groups:
  - "-1234567890"  # Your group chat ID

ask_rules: |
  - Rules for /ask command

feat_rules: |
  - Rules for /feat command

fix_rules: |
  - Rules for /fix command

feedback_rules: |
  - Rules for /feedback command

projects:
  - project_name: "my-project"
    project_repo: "git@github.com:user/repo.git"
    project_workdir: "/path/to/workdir"
```

## Usage

### Start the Bot

```bash
./tg-claude --api-token YOUR_TELEGRAM_BOT_TOKEN
```

### Available Commands

#### `/ask <query>`
General questions and quick tasks without project context.

**Example:**
```
/ask Explain how async/await works in Python
```

#### `/feat <project-name> <task>`
Implement new features in a specific project. Creates a new branch and merge request.

**Example:**
```
/feat my-project Add user authentication with JWT
```

#### `/feedback <project-name> <feedback>`
Continue work on an existing branch with feedback.

**Example:**
```
/feedback my-project Fix the validation on the login form
```

#### `/fix <project-name> <issue>`
Fix bugs in a specific project (currently placeholder).

**Example:**
```
/fix my-project Resolve the memory leak in the cache module
```

## How It Works

1. User sends a command in an authorized Telegram group
2. Bot validates user and group authorization
3. Bot clones repository if needed (for project commands)
4. Bot executes Claude Code with:
   - `--dangerously-skip-permissions` flag
   - `--verbose` flag for detailed output
   - `--system-prompt` with command-specific rules
5. Bot tracks execution time
6. Bot sends results back to the chat
7. Output is saved to `output.txt` with execution time appended

## Timeout Configuration

All operations have a **30-minute timeout**:
- Claude command execution: 30 minutes
- Git clone operations: 30 minutes

## Authorization

The bot enforces **dual authorization**:
- User must be in `authorized_users` list
- Chat must be in `authorized_groups` list

Both conditions must be met for the bot to respond.

## Project Structure

```
tg-claude/
├── tg-claude           # Main bot script (executable)
├── projects.yaml       # Configuration file
├── requirements.txt    # Python dependencies
├── README.md          # This file
└── output.txt         # Generated output (per execution)
```

## Dependencies

- `python-telegram-bot==20.7` - Telegram Bot API wrapper
- `PyYAML==6.0.1` - YAML configuration parser

## Getting Your Telegram Bot Token

1. Open Telegram and search for @BotFather
2. Send `/newbot` and follow the instructions
3. Copy the API token provided
4. To get group chat ID:
   - Add the bot to your group
   - Send a message in the group
   - Visit: `https://api.telegram.org/bot<YourBOTToken>/getUpdates`
   - Find the chat ID in the response (negative number for groups)

## Disable Privacy Mode

For the bot to work in groups, you must disable Privacy Mode:

1. Open @BotFather
2. Send `/mybots`
3. Select your bot
4. Go to **Bot Settings** → **Group Privacy**
5. Click **Turn off**
6. Remove and re-add the bot to your group

## Logging

The bot logs all operations including:
- User authorization attempts
- Command executions
- Git operations
- Execution durations
- Errors and timeouts

Logs are output to stdout with timestamps.

## Troubleshooting

### Bot doesn't respond in group
- Check that Privacy Mode is disabled in @BotFather
- Verify the group chat ID is in `authorized_groups`
- Verify your username is in `authorized_users`
- Check bot logs for authorization messages

### Git clone fails
- Verify SSH keys are configured if using SSH URLs
- Ensure the bot has network access
- Check repository URL is correct

### Command times out
- Increase timeout if needed (currently 30 minutes)
- Check Claude Code is installed and accessible
- Verify the task isn't too complex

### Unauthorized user message
- Check your Telegram username matches exactly (case-sensitive)
- Ensure you're in the correct group
- Verify `projects.yaml` is loaded correctly (check startup logs)

## Security Considerations

1. **Isolated Environment**: Always run in isolation
2. **API Token**: Keep your Telegram bot token secret
3. **Authorization**: Regularly review authorized users and groups
4. **Repository Access**: Bot has full access to configured repositories
5. **File System**: Bot can modify files in project directories
6. **Command Execution**: Bot executes arbitrary commands via Claude

## Contributing

This project is in active development. Please test thoroughly before contributing and document any changes.

## License

MIT

## Disclaimer

This bot executes AI-generated code with elevated permissions. Use at your own risk. Always review outputs and changes made by the bot. The authors are not responsible for any damage or data loss caused by using this bot.

---

**Built with Claude Code** 🤖
