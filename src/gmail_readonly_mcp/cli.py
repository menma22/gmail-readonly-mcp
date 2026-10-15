from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from platformdirs import user_data_path

READONLY = "https://www.googleapis.com/auth/gmail.readonly"
IDENTITY = {
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
}
TOOLS = {
    "search_gmail_messages",
    "get_gmail_message_content",
    "get_gmail_messages_content_batch",
    "get_gmail_attachment_content",
    "get_gmail_thread_content",
    "get_gmail_threads_content_batch",
    "list_gmail_labels",
}
FLAGS = [
    "--single-user",
    "--read-only",
    "--tools",
    "gmail",
    "--tool-tier",
    "complete",
    "--disabled-tools",
    "start_google_auth",
]


class SetupError(Exception):
    pass


def protect_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise SetupError("The profile directory must not be a symbolic link.")
    if os.name == "nt":
        identity = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
        ).stdout
        sid = re.search(rb"S-1-\d+(?:-\d+)+", identity)
        if not sid:
            raise SetupError("Could not determine the current Windows user's SID.")
        result = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"*{sid.group().decode('ascii')}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
            ],
            capture_output=True,
        )
        if result.returncode:
            raise SetupError("Could not restrict the profile directory's Windows ACL.")
    else:
        path.chmod(0o700)


def write_private(path: Path, data: dict) -> None:
    if path.is_symlink():
        raise SetupError("Refusing to write credentials through a symbolic link.")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    if os.name != "nt":
        path.chmod(0o600)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise SetupError(
            "Could not read a required JSON file; check its path and format."
        ) from None
    if not isinstance(value, dict):
        raise SetupError("Expected a JSON object.")
    return value


def profile_path(state_dir: Path, name: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name):
        raise SetupError("Profile names must be 1–32 lowercase letters, digits, '_' or '-'.")
    return state_dir.expanduser().resolve() / name


def account(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[^\s@/\\]+@[^\s@/\\]+\.[^\s@/\\]+", value):
        raise SetupError("Use the primary email address of your Google account.")
    if any(char in value for char in '<>:"|?*%'):
        raise SetupError("This email address cannot be used safely as a credential filename.")
    return value


def load_profile(path: Path) -> dict:
    if path.is_symlink():
        raise SetupError("The profile directory must not be a symbolic link.")
    value = read_json(path / "profile.json")
    value["account"] = account(value.get("account", ""))
    return value


def credential_path(path: Path, email: str) -> Path:
    return path / f"{quote(email, safe='@._-')}.json"


def validate_client(data: dict) -> dict:
    client = data.get("installed")
    if not isinstance(client, dict):
        raise SetupError("Download an OAuth client of type Desktop app from Google Cloud.")
    if not client.get("client_id", "").endswith(".apps.googleusercontent.com"):
        raise SetupError("The OAuth client ID is not a Google client ID.")
    if not client.get("client_secret"):
        raise SetupError("The Desktop OAuth client JSON has no client_secret.")
    if client.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth":
        raise SetupError("Refusing a non-Google authorization endpoint.")
    if client.get("token_uri") != "https://oauth2.googleapis.com/token":
        raise SetupError("Refusing a non-Google token endpoint.")
    return {"installed": client}


def google_json(url: str, **kwargs) -> dict:
    try:
        response = requests.get(url, timeout=20, allow_redirects=False, **kwargs)
        if response.status_code != 200:
            if response.status_code in {403, 429}:
                reasons = {
                    item.get("reason")
                    for item in response.json().get("error", {}).get("errors", [])
                }
                if reasons & {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}:
                    raise SetupError(
                        "Google API quota exceeded; wait before retrying verification."
                    )
            raise SetupError(f"Google verification failed (HTTP {response.status_code}).")
        return response.json()
    except (requests.RequestException, ValueError):
        raise SetupError("Could not verify the grant with Google; refusing to continue.") from None


def verify_grant(credentials: Credentials, expected: str) -> dict:
    if credentials.token_uri != "https://oauth2.googleapis.com/token":
        raise SetupError("Refusing a credential with a non-Google token endpoint.")
    if not credentials.valid:
        try:
            credentials.refresh(Request())
        except Exception:
            raise SetupError("Google token refresh failed. Run auth again.") from None
    info = google_json(
        "https://oauth2.googleapis.com/tokeninfo",
        params={"access_token": credentials.token},
    )
    scopes = set(info.get("scope", "").split())
    if READONLY not in scopes or not scopes <= {READONLY} | IDENTITY:
        raise SetupError(
            "The actual token has missing or additional data scopes; reconnect read-only."
        )
    if info.get("aud") and info["aud"] != credentials.client_id:
        raise SetupError("The token belongs to a different OAuth client.")
    profile = google_json(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {credentials.token}"},
    )
    actual = profile.get("emailAddress", "").lower()
    if actual != expected:
        raise SetupError("The signed-in Google account does not match this profile.")
    return {"account": actual, "granted_scopes": sorted(scopes), "read_only": True}


def checked_credentials(path: Path, settings: dict) -> tuple[Credentials, dict]:
    client = validate_client(read_json(path / "client_secret.json"))["installed"]
    data = read_json(credential_path(path, settings["account"]))
    if data.get("client_id") != client["client_id"]:
        raise SetupError("The cached token and Desktop OAuth client do not match.")
    try:
        credentials = Credentials.from_authorized_user_info(data)
    except (ValueError, TypeError):
        raise SetupError("The credential file is invalid. Run auth again.") from None
    report = verify_grant(credentials, settings["account"])
    write_private(credential_path(path, settings["account"]), json.loads(credentials.to_json()))
    return credentials, report


def upstream_command() -> list[str]:
    from importlib.metadata import version

    if version("workspace-mcp") != "2.1.0":
        raise SetupError(
            "This release requires workspace-mcp 2.1.0; reinstall locked dependencies."
        )
    return [sys.executable, "-c", "from main import main; main()", *FLAGS]


def upstream_env(path: Path, settings: dict) -> dict[str, str]:
    prefixes = ("WORKSPACE_", "GOOGLE_", "MCP_", "OAUTHLIB_", "OTEL_")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(prefixes) and key not in {"USER_GOOGLE_EMAIL", "PORT"}
    }
    env.update(
        {
            "WORKSPACE_MCP_CREDENTIALS_DIR": str(path),
            "GOOGLE_CLIENT_SECRET_PATH": str(path / "client_secret.json"),
            "USER_GOOGLE_EMAIL": settings["account"],
            "WORKSPACE_MCP_PORT": "18769",
            "PYTHONUTF8": "1",
        }
    )
    return env


def run_upstream(path: Path, settings: dict) -> None:
    from importlib import import_module

    upstream_command()
    env = upstream_env(path, settings)
    os.chdir(path)
    os.environ.clear()
    os.environ.update(env)
    sys.argv = ["workspace-mcp", *FLAGS]
    import_module("main").main()


def init_profile(args, path: Path) -> dict:
    email = account(args.account)
    client = validate_client(read_json(args.client_secrets))
    if path.exists():
        raise SetupError(
            "This profile already exists. Use a new profile name to avoid overwriting it."
        )
    protect_directory(path)
    write_private(path / "client_secret.json", client)
    write_private(path / "profile.json", {"account": email})
    return {"initialized": True, "profile": args.profile, "state_directory": str(path)}


def authenticate(path: Path, settings: dict, no_browser: bool) -> dict:
    client = validate_client(read_json(path / "client_secret.json"))
    flow = InstalledAppFlow.from_client_config(
        client,
        scopes=[READONLY],
        autogenerate_code_verifier=True,
    )
    try:
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            open_browser=not no_browser,
            timeout_seconds=300,
            access_type="offline",
            prompt="consent select_account",
            login_hint=settings["account"],
            include_granted_scopes="false",
            success_message="Gmail read-only authorization received. Return to your terminal.",
        )
    except Exception:
        raise SetupError(
            "Google authorization did not complete. Retry auth; details are not logged."
        ) from None
    report = verify_grant(credentials, settings["account"])
    if not credentials.refresh_token:
        raise SetupError("No refresh token was returned. Retry auth and approve offline access.")
    write_private(credential_path(path, settings["account"]), json.loads(credentials.to_json()))
    return report


async def probe(path: Path, settings: dict, read_message: bool) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = upstream_command()
    parameters = StdioServerParameters(
        command=command[0],
        args=command[1:],
        env=upstream_env(path, settings),
        cwd=str(path),
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.list_tools()
            names = {tool.name for tool in result.tools}
            if names != TOOLS:
                raise SetupError(
                    "The upstream tool catalog changed; refusing to certify it read-only."
                )
            labels = await session.call_tool("list_gmail_labels", {})
            if labels.is_error:
                raise SetupError("The MCP label-read check failed.")
            report = {"mcp_tools": sorted(names), "mcp_label_read": True}
            if read_message:
                search = await session.call_tool(
                    "search_gmail_messages",
                    {"query": "-in:spam -in:trash", "page_size": 1},
                )
                if search.is_error:
                    raise SetupError("The MCP search check failed.")
                text = "\n".join(item.text for item in search.content if hasattr(item, "text"))
                found = re.search(r"Message ID:\s*([0-9a-f]{12,})", text)
                report["mcp_search"] = True
                report["mcp_message_read"] = False
                if found:
                    message = await session.call_tool(
                        "get_gmail_message_content",
                        {"message_id": found.group(1)},
                    )
                    if message.is_error:
                        raise SetupError("The MCP message-read check failed.")
                    report["mcp_message_read"] = True
                else:
                    report["message_read_note"] = (
                        "No matching mail; no message-read claim was made."
                    )
            return report


def configuration(args, state_dir: Path) -> str:
    command = [
        "-m",
        "gmail_readonly_mcp",
        "--state-dir",
        str(state_dir),
        "serve",
        "--profile",
        args.profile,
    ]
    name = f"gmail-readonly-{args.profile}"
    if args.format == "codex":
        return (
            f"[mcp_servers.{name}]\ncommand = {json.dumps(sys.executable)}\n"
            f"args = {json.dumps(command)}\nstartup_timeout_sec = 60\n"
            f"enabled_tools = {json.dumps(sorted(TOOLS))}\n"
        )
    return json.dumps(
        {"mcpServers": {name: {"command": sys.executable, "args": command}}}, indent=2
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Set up account-isolated Gmail read-only MCP.")
    result.add_argument(
        "--state-dir", type=Path, default=user_data_path("gmail-readonly-mcp", appauthor=False)
    )
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("init", "auth", "verify", "config", "serve"):
        sub = commands.add_parser(name)
        sub.add_argument("--profile", required=True)
        if name == "init":
            sub.add_argument("--account", required=True)
            sub.add_argument("--client-secrets", type=Path, required=True)
        elif name == "auth":
            sub.add_argument("--no-browser", action="store_true")
        elif name == "verify":
            sub.add_argument("--read-message", action="store_true")
        elif name == "config":
            sub.add_argument("--format", choices=("codex", "json"), default="codex")
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        state_dir = args.state_dir.expanduser().resolve()
        path = profile_path(state_dir, args.profile)
        if args.command == "init":
            output = init_profile(args, path)
        else:
            settings = load_profile(path)
            if args.command == "config":
                print(configuration(args, state_dir))
                return
            protect_directory(path)
            if args.command == "auth":
                output = authenticate(path, settings, args.no_browser)
            else:
                _, output = checked_credentials(path, settings)
                if args.command == "serve":
                    run_upstream(path, settings)
                    return
                output.update(asyncio.run(probe(path, settings, args.read_message)))
        print(json.dumps(output, ensure_ascii=False, indent=2))
    except SetupError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("Error: operation failed; credential details are not logged.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
