import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
import requests
from google.oauth2.credentials import Credentials

from gmail_readonly_mcp import cli

EMAIL = "reader@example.com"


def credentials():
    return Credentials(
        token="test-access-value",
        refresh_token="test-refresh-value",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test.apps.googleusercontent.com",
        client_secret="test-client-value",
        scopes=[cli.READONLY],
        expiry=datetime.now() + timedelta(hours=1),
    )


def client_config():
    return {
        "installed": {
            "client_id": "test.apps.googleusercontent.com",
            "client_secret": "test-client-value",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def mock_google(monkeypatch, scopes, email=EMAIL):
    responses = [
        {"scope": " ".join(scopes), "aud": "test.apps.googleusercontent.com"},
        {"emailAddress": email},
    ]
    monkeypatch.setattr(cli, "google_json", Mock(side_effect=responses))


@pytest.mark.parametrize("scopes", [{cli.READONLY}, {cli.READONLY} | cli.IDENTITY])
def test_actual_google_grant_accepts_only_mail_readonly(monkeypatch, scopes):
    mock_google(monkeypatch, scopes)
    assert cli.verify_grant(credentials(), EMAIL)["read_only"] is True


@pytest.mark.parametrize(
    "extra",
    [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
        "https://mail.google.com/",
        "https://www.googleapis.com/auth/drive.readonly",
        "unknown.scope",
    ],
)
def test_any_extra_data_grant_is_rejected_before_profile_read(monkeypatch, extra):
    mock_google(monkeypatch, {cli.READONLY, extra})
    with pytest.raises(cli.SetupError, match="additional"):
        cli.verify_grant(credentials(), EMAIL)
    assert cli.google_json.call_count == 1


def test_missing_mail_grant_is_rejected(monkeypatch):
    mock_google(monkeypatch, {"openid"})
    with pytest.raises(cli.SetupError):
        cli.verify_grant(credentials(), EMAIL)


def test_wrong_signed_in_account_is_rejected(monkeypatch):
    mock_google(monkeypatch, {cli.READONLY}, "other@example.com")
    with pytest.raises(cli.SetupError, match="does not match"):
        cli.verify_grant(credentials(), EMAIL)


def test_wrong_token_audience_is_rejected(monkeypatch):
    monkeypatch.setattr(
        cli,
        "google_json",
        Mock(
            return_value={
                "scope": cli.READONLY,
                "aud": "other.apps.googleusercontent.com",
            }
        ),
    )
    with pytest.raises(cli.SetupError, match="different OAuth"):
        cli.verify_grant(credentials(), EMAIL)


def test_bad_token_endpoint_is_rejected_before_refresh(monkeypatch):
    item = credentials()
    item._token_uri = "https://example.invalid/token"
    item.expiry = datetime.now() - timedelta(hours=1)
    monkeypatch.setattr(item, "refresh", Mock())
    with pytest.raises(cli.SetupError, match="non-Google"):
        cli.verify_grant(item, EMAIL)
    item.refresh.assert_not_called()


def test_network_error_never_exposes_access_token(monkeypatch):
    monkeypatch.setattr(
        cli.requests,
        "get",
        Mock(
            side_effect=requests.ConnectionError(
                "https://oauth2.googleapis.com/tokeninfo?access_token=test-access-value",
            )
        ),
    )
    with pytest.raises(cli.SetupError) as caught:
        cli.verify_grant(credentials(), EMAIL)
    assert "test-access-value" not in str(caught.value)


def test_google_redirect_is_rejected(monkeypatch):
    response = Mock(status_code=302)
    monkeypatch.setattr(cli.requests, "get", Mock(return_value=response))
    with pytest.raises(cli.SetupError, match="HTTP 302"):
        cli.google_json("https://oauth2.googleapis.com/tokeninfo")
    assert cli.requests.get.call_args.kwargs["allow_redirects"] is False


def test_google_quota_error_is_distinguished_without_echoing_response(monkeypatch):
    response = Mock(status_code=403)
    response.json.return_value = {
        "error": {"message": "private diagnostic", "errors": [{"reason": "rateLimitExceeded"}]}
    }
    monkeypatch.setattr(cli.requests, "get", Mock(return_value=response))
    with pytest.raises(cli.SetupError, match="quota exceeded") as caught:
        cli.google_json("https://gmail.googleapis.com/gmail/v1/users/me/profile")
    assert "private diagnostic" not in str(caught.value)


@pytest.mark.parametrize("name", ["../other", "a/b", "A", "", "/tmp/profile", "a" * 33])
def test_profile_names_cannot_escape_state_directory(tmp_path, name):
    with pytest.raises(cli.SetupError):
        cli.profile_path(tmp_path, name)


def test_profiles_keep_separate_storage(tmp_path):
    assert cli.profile_path(tmp_path, "work") != cli.profile_path(tmp_path, "personal")


def test_plus_address_matches_upstream_filename(tmp_path):
    assert (
        cli.credential_path(tmp_path, "reader+tag@example.com").name
        == "reader%2Btag@example.com.json"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("auth_uri", "https://example.invalid/auth"),
        ("token_uri", "https://example.invalid/token"),
        ("client_id", "not-a-google-client"),
        ("client_secret", ""),
    ],
)
def test_client_import_rejects_invalid_configuration(field, value):
    data = client_config()
    data["installed"][field] = value
    with pytest.raises(cli.SetupError):
        cli.validate_client(data)


def test_web_client_is_rejected():
    with pytest.raises(cli.SetupError, match="Desktop"):
        cli.validate_client({"web": client_config()["installed"]})


def test_init_will_not_overwrite_existing_profile(tmp_path):
    source = tmp_path / "download.json"
    source.write_text(json.dumps(client_config()), encoding="utf-8")
    profile = tmp_path / "work"
    profile.mkdir()
    sentinel = profile / "keep"
    sentinel.write_text("existing data", encoding="utf-8")
    args = argparse.Namespace(account=EMAIL, client_secrets=source, profile="work")
    with pytest.raises(cli.SetupError, match="already exists"):
        cli.init_profile(args, profile)
    assert sentinel.read_text() == "existing data"


def test_init_saves_only_local_profile_files(tmp_path):
    source = tmp_path / "download.json"
    source.write_text(json.dumps(client_config()), encoding="utf-8")
    profile = tmp_path / "work"
    args = argparse.Namespace(account=EMAIL, client_secrets=source, profile="work")
    cli.init_profile(args, profile)
    assert cli.load_profile(profile)["account"] == EMAIL
    if os.name != "nt":
        assert profile.stat().st_mode & 0o777 == 0o700
        assert (profile / "client_secret.json").stat().st_mode & 0o777 == 0o600


def test_environment_cannot_widen_scopes_or_change_account(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_PERMISSIONS", "gmail:full")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "other-client")
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "other@example.com")
    env = cli.upstream_env(tmp_path, {"account": EMAIL})
    assert "WORKSPACE_MCP_PERMISSIONS" not in env
    assert "GOOGLE_OAUTH_CLIENT_ID" not in env
    assert "MCP_ENABLE_OAUTH21" not in env
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env
    assert env["USER_GOOGLE_EMAIL"] == EMAIL
    assert "--read-only" in cli.FLAGS
    assert cli.FLAGS[cli.FLAGS.index("--tools") + 1] == "gmail"
    assert cli.FLAGS[-2:] == ["--disabled-tools", "start_google_auth"]


def test_generated_codex_configuration_parses(tmp_path):
    import tomllib

    args = argparse.Namespace(profile="work", format="codex")
    data = tomllib.loads(cli.configuration(args, tmp_path))
    server = data["mcp_servers"]["gmail-readonly-work"]
    assert set(server["enabled_tools"]) == cli.TOOLS
    assert "serve" in server["args"]
    assert "client_secret" not in cli.configuration(args, tmp_path)


def test_generated_json_configuration_parses(tmp_path):
    args = argparse.Namespace(profile="personal", format="json")
    server = json.loads(cli.configuration(args, tmp_path))["mcpServers"]["gmail-readonly-personal"]
    assert server["args"][-1] == "personal"


def test_auth_requests_only_gmail_and_checks_grant_before_saving(tmp_path, monkeypatch):
    (tmp_path / "client_secret.json").write_text(json.dumps(client_config()), encoding="utf-8")
    flow = Mock()
    flow.run_local_server.return_value = credentials()
    factory = Mock(return_value=flow)
    monkeypatch.setattr(cli.InstalledAppFlow, "from_client_config", factory)
    mock_google(monkeypatch, {cli.READONLY})
    cli.authenticate(tmp_path, {"account": EMAIL}, True)
    assert factory.call_args.kwargs["scopes"] == [cli.READONLY]
    assert factory.call_args.kwargs["autogenerate_code_verifier"] is True
    assert flow.run_local_server.call_args.kwargs["include_granted_scopes"] == "false"
    assert cli.credential_path(tmp_path, EMAIL).exists()


def test_failed_account_check_does_not_cache_new_credential(tmp_path, monkeypatch):
    (tmp_path / "client_secret.json").write_text(json.dumps(client_config()), encoding="utf-8")
    flow = Mock()
    flow.run_local_server.return_value = credentials()
    monkeypatch.setattr(cli.InstalledAppFlow, "from_client_config", Mock(return_value=flow))
    mock_google(monkeypatch, {cli.READONLY}, "other@example.com")
    with pytest.raises(cli.SetupError):
        cli.authenticate(tmp_path, {"account": EMAIL}, True)
    assert not cli.credential_path(tmp_path, EMAIL).exists()


def test_serve_refuses_failed_grant_before_starting_upstream(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gmail-readonly-mcp",
            "--state-dir",
            str(tmp_path),
            "serve",
            "--profile",
            "work",
        ],
    )
    monkeypatch.setattr(cli, "load_profile", Mock(return_value={"account": EMAIL}))
    monkeypatch.setattr(cli, "protect_directory", Mock())
    monkeypatch.setattr(cli, "checked_credentials", Mock(side_effect=cli.SetupError("blocked")))
    start = Mock()
    monkeypatch.setattr(cli, "run_upstream", start)
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 1
    start.assert_not_called()
    assert capsys.readouterr().out == ""


def test_real_upstream_startup_with_spaces_and_exact_readonly_catalog(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    profile = tmp_path / "profile with spaces"
    profile.mkdir()
    script = (
        "import sys; from pathlib import Path; "
        "from gmail_readonly_mcp.cli import run_upstream; "
        "run_upstream(Path(sys.argv[1]), {'account': 'reader@example.com'})"
    )

    async def check():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-c", script, str(profile)],
            cwd=str(profile),
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                assert {tool.name for tool in (await session.list_tools()).tools} == cli.TOOLS

    asyncio.run(asyncio.wait_for(check(), timeout=60))
