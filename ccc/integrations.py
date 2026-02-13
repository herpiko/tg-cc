"""MCP server integration builders for ccc bot."""

import logging
import os
import shutil

logger = logging.getLogger(__name__)


def _build_gspreadsheet(entry: dict) -> tuple[str, dict, list[str]]:
    """Build MCP config for Google Sheets integration.

    Args:
        entry: Config entry with 'service_account' and optional 'drive_folder_id'.

    Returns:
        Tuple of (server_name, server_config, tool_patterns)
    """
    service_account = entry.get("service_account")
    if not service_account:
        raise ValueError("gspreadsheet integration requires 'service_account' path")

    env = {"SERVICE_ACCOUNT_PATH": service_account}

    drive_folder_id = entry.get("drive_folder_id")
    if drive_folder_id:
        env["DRIVE_FOLDER_ID"] = drive_folder_id

    # Find uvx absolute path
    uvx_cmd = _resolve_command("uvx")

    server_config = {
        "command": uvx_cmd,
        "args": ["mcp-google-sheets@latest"],
        "env": env,
    }

    tool_patterns = ["mcp__gspreadsheet__*"]

    return "gspreadsheet", server_config, tool_patterns


def _resolve_command(cmd: str) -> str:
    """Resolve a command to its absolute path, with common fallback locations."""
    full_path = shutil.which(cmd)
    if full_path:
        return full_path

    # Check common install locations
    home = os.path.expanduser("~")
    fallback_paths = [
        os.path.join(home, ".local", "bin", cmd),
        os.path.join("/usr", "local", "bin", cmd),
    ]
    for path in fallback_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            logger.info("Found %s at fallback location: %s", cmd, path)
            return path

    logger.warning("Could not find absolute path for '%s', using as-is", cmd)
    return cmd


# Registry of known integrations: name -> builder function
KNOWN_INTEGRATIONS = {
    "gspreadsheet": _build_gspreadsheet,
}


# Descriptions of known integrations for system prompts
INTEGRATION_DESCRIPTIONS = {
    "gspreadsheet": "Google Sheets — you can search, read, and write spreadsheets in Google Drive. Use mcp__gspreadsheet__* tools DIRECTLY (do NOT delegate to sub-agents, they cannot access MCP tools).",
}


def build_mcp_config(integrations: list[dict], config_dir: str = None) -> tuple[dict, list[str]]:
    """Build MCP server configs and allowed tools from integrations config.

    Args:
        integrations: List of integration entries from config.yaml.
        config_dir: Directory of the config file, for resolving relative paths.

    Returns:
        Tuple of (mcp_servers_dict, allowed_tools_list)
    """
    mcp_servers = {}
    allowed_tools = []

    for entry in integrations:
        mcp_name = entry.get("mcp")
        if not mcp_name:
            logger.warning("Integration entry missing 'mcp' key, skipping: %s", entry)
            continue

        # Resolve relative file paths in the entry relative to config directory
        if config_dir:
            _resolve_relative_paths(entry, config_dir)

        if mcp_name in KNOWN_INTEGRATIONS:
            # Known integration: use builder
            try:
                server_name, server_config, tool_patterns = KNOWN_INTEGRATIONS[mcp_name](entry)
                mcp_servers[server_name] = server_config
                allowed_tools.extend(tool_patterns)
                logger.info("Loaded known integration: %s", mcp_name)
            except Exception as e:
                logger.error("Failed to build integration '%s': %s", mcp_name, e)
        else:
            # Generic MCP server
            command = entry.get("command")
            url = entry.get("url")

            if command:
                # stdio server - resolve command path
                server_config = {"command": _resolve_command(command)}
                args = entry.get("args")
                if args:
                    server_config["args"] = args
                env = entry.get("env")
                if env:
                    server_config["env"] = env
            elif url:
                # SSE or HTTP server
                server_type = entry.get("type", "sse")
                server_config = {"type": server_type, "url": url}
                headers = entry.get("headers")
                if headers:
                    server_config["headers"] = headers
            else:
                logger.warning(
                    "Generic integration '%s' needs 'command' (stdio) or 'url' (sse/http), skipping",
                    mcp_name,
                )
                continue

            mcp_servers[mcp_name] = server_config
            allowed_tools.append(f"mcp__{mcp_name}__*")
            logger.info("Loaded generic integration: %s", mcp_name)

    return mcp_servers, allowed_tools


def _resolve_relative_paths(entry: dict, config_dir: str):
    """Resolve relative file paths in an integration entry to absolute paths."""
    path_keys = ["service_account"]
    for key in path_keys:
        value = entry.get(key)
        if value and not os.path.isabs(value):
            resolved = os.path.abspath(os.path.join(config_dir, value))
            logger.info("Resolved %s: %s -> %s", key, value, resolved)
            entry[key] = resolved


def get_integration_descriptions() -> str:
    """Get a description of loaded MCP integrations for system prompts.

    Returns:
        A string describing available integrations, or empty string if none.
    """
    from . import config as cfg
    if not cfg.MCP_SERVERS:
        return ""

    lines = [
        "IMPORTANT: You have access to the following MCP integrations. "
        "You MUST use these tools directly when the user's request involves them. "
        "Do NOT say you cannot access these services — you CAN via the MCP tools below:"
    ]
    for server_name in cfg.MCP_SERVERS:
        desc = INTEGRATION_DESCRIPTIONS.get(server_name, f"{server_name} MCP server")
        lines.append(f"  - {desc}")
    return "\n".join(lines)


def validate_integrations() -> list[str]:
    """Validate MCP integration prerequisites at startup.

    Returns:
        List of warning/error messages. Empty if all is well.
    """
    from . import config as cfg
    issues = []

    for server_name, server_config in cfg.MCP_SERVERS.items():
        command = server_config.get("command", "")
        env = server_config.get("env", {})

        # Check command exists
        if command and not os.path.isabs(command):
            resolved = shutil.which(command)
            if not resolved:
                issues.append(f"[{server_name}] Command '{command}' not found in PATH")
        elif command and not os.path.isfile(command):
            issues.append(f"[{server_name}] Command not found: {command}")

        # Check service account / credential files in env
        for env_key, env_value in env.items():
            if "path" in env_key.lower() or "credentials" in env_key.lower() or "account" in env_key.lower():
                if env_value and not os.path.isfile(env_value):
                    issues.append(f"[{server_name}] File not found for {env_key}: {env_value}")

    return issues
