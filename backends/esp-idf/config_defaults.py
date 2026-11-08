"""Compose compile-time configuration overlays for the ESP-IDF backend.

All public compile-time settings live in `config/build.defaults`. This module
only appends generated overlays (profile, credentials) and validates that the
resulting sdkconfig matches the requested profile. Secrets are read from an
explicit path at build time and are never echoed, logged or copied into the
source tree.
"""

from __future__ import annotations

import json
from pathlib import Path

PLACEHOLDER_SSID = "COMPILE_ONLY_NO_NETWORK"
PLACEHOLDER_TOKEN = "COMPILE_ONLY_NOT_A_SECRET"

# Accepted credential keys (case-insensitive); positional files are also allowed.
SSID_KEYS = {"ssid", "wifi_ssid", "wifi", "network"}
PASSWORD_KEYS = {"password", "pass", "psk", "wifi_password"}
TOKEN_KEYS = {"token", "lan_token", "auth_token"}

WATCHDOG_REQUIRED = (
    "CONFIG_ESP_INT_WDT",
    "CONFIG_ESP_TASK_WDT_EN",
    "CONFIG_ESP_TASK_WDT_PANIC",
)


class ConfigError(ValueError):
    """Configuration input is unusable; message never contains a secret."""


def _field(text, label):
    """Validate one credential field without revealing its value."""
    value = text.strip()
    if value[:1] == value[-1:] and value[:1] in ("'", '"') and len(value) >= 2:
        value = value[1:-1]
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ConfigError(label + " must be a single line without NUL")
    return value


def load_credentials(path):
    """Parse a private credentials file into SSID/password/token.

    Two shapes are accepted, chosen by the first meaningful line:

    * ``key = value`` / ``key: value`` with keys ssid/password/token (aliases
      above). Unknown keys are ignored so a file may carry extra notes.
    * positional: line 1 is the SSID, line 2 the password, optional line 3 the
      token. This is the plain ``wifi.pw`` shape (two bare lines).

    Comments start with ``#``. Raises ConfigError on unusable input; the
    message names the field only, never the value.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError("credentials file does not exist: " + str(path)) from None
    except UnicodeError:
        raise ConfigError("credentials file must be UTF-8 text") from None
    lines = [
        line for line in raw.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise ConfigError("credentials file has no usable lines")

    found = {}
    keyed = any("=" in line or ":" in line for line in lines)
    if keyed:
        for line in lines:
            separator = min(
                (index for index in (line.find("="), line.find(":")) if index >= 0), default=-1
            )
            if separator < 0:
                raise ConfigError("mixed keyed and positional credentials are ambiguous")
            key = line[:separator].strip().lower()
            value = _field(line[separator + 1 :], key or "value")
            if key in SSID_KEYS:
                found["ssid"] = value
            elif key in PASSWORD_KEYS:
                found["password"] = value
            elif key in TOKEN_KEYS:
                found["token"] = value
    else:
        if len(lines) > 3:
            raise ConfigError("positional credentials take at most ssid/password/token lines")
        found["ssid"] = _field(lines[0], "ssid")
        if len(lines) > 1:
            found["password"] = _field(lines[1], "password")
        if len(lines) > 2:
            found["token"] = _field(lines[2], "token")

    if not found.get("ssid"):
        raise ConfigError("credentials file needs an SSID")
    validate_credentials(found)
    return found


def validate_credentials(credentials):
    """Range-check the fields that were supplied; empty values mean "not given"."""
    password = credentials.get("password", "")
    if password and not 8 <= len(password) <= 63:
        raise ConfigError("password must be 8..63 bytes for WPA2")
    token = credentials.get("token", "")
    if token and not 16 <= len(token) <= 128:
        raise ConfigError("token must be 16..128 bytes")
    return credentials


def complete_credentials(credentials, token_fallback=None):
    """Fill a missing token from an explicit environment fallback, then re-check.

    A token is the one field users commonly keep outside the network file, so
    ``RMB_TOKEN`` is accepted as a documented fallback. Callers pass it in;
    this module never reads the environment itself.
    """
    if "token" not in credentials and token_fallback:
        credentials = dict(credentials, token=token_fallback.strip())
        if "\n" in credentials["token"] or "\r" in credentials["token"]:
            raise ConfigError("RMB_TOKEN must be a single line")
    return validate_credentials(credentials)


def kconfig_string(value):
    """Render a Kconfig string literal without breaking quoting or escaping."""
    return json.dumps(value, ensure_ascii=False)


def credential_lines(credentials):
    """Overlay lines for values supplied at build time (highest reasonable priority)."""
    lines = ["# Generated from --credentials-file; never commit this output."]
    if credentials.get("ssid"):
        lines.append("CONFIG_RMB_WIFI_SSID=" + kconfig_string(credentials["ssid"]))
    if "password" in credentials:
        lines.append("CONFIG_RMB_WIFI_PASSWORD=" + kconfig_string(credentials["password"]))
    if credentials.get("token"):
        lines.append("CONFIG_RMB_TOKEN=" + kconfig_string(credentials["token"]))
    return lines


def profile_lines(profile):
    """Overlay lines for the requested build profile."""
    if profile == "compile-enabled":
        return []
    return [
        "# safe profile: no resident networking or module loading",
        "# CONFIG_RMB_ENABLE is not set",
    ]


def read_values(config_text):
    """Parse a generated sdkconfig into a CONFIG_* mapping (strings kept raw)."""
    values = {}
    for line in config_text.splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def unquote(value):
    """Decode a Kconfig string literal to the configured text."""
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    return value


def validate(values, profile):
    """Reject a configuration that cannot run the requested profile safely."""
    enabled = values.get("CONFIG_RMB_ENABLE") == "y"
    if enabled != (profile == "compile-enabled"):
        raise ConfigError(
            "generated sdkconfig service enable does not match the selected profile; "
            "use a fresh build directory or correct the configuration"
        )
    if not enabled:
        return
    for key in WATCHDOG_REQUIRED:
        if values.get(key) != "y":
            raise ConfigError(
                "enabled resident requires IWDT, TWDT panic and panic reboot: missing " + key
            )
    if not any(
        values.get(key) == "y"
        for key in ("CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT", "CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT")
    ):
        raise ConfigError("enabled resident requires reboot-on-panic")
    if not values.get("CONFIG_RMB_WIFI_SSID") or unquote(values["CONFIG_RMB_WIFI_SSID"]) in (
        "",
        PLACEHOLDER_SSID,
    ):
        raise ConfigError("resident needs a real Wi-Fi SSID; pass --credentials-file")
    token = unquote(values.get("CONFIG_RMB_TOKEN", ""))
    if len(token) < 16 or token == PLACEHOLDER_TOKEN:
        raise ConfigError("resident needs a 16..128 byte token; pass --credentials-file")


def describe(values):
    """Redacted configuration summary: shapes and lengths only, never values."""
    ssid = unquote(values.get("CONFIG_RMB_WIFI_SSID", ""))
    token = unquote(values.get("CONFIG_RMB_TOKEN", ""))
    placeholder = ssid in ("", PLACEHOLDER_SSID) or token in ("", PLACEHOLDER_TOKEN)
    return {
        "resident_enabled": values.get("CONFIG_RMB_ENABLE") == "y",
        "ssid_configured": ssid not in ("", PLACEHOLDER_SSID),
        "ssid_bytes": len(ssid),
        "password_configured": bool(unquote(values.get("CONFIG_RMB_WIFI_PASSWORD", ""))),
        "token_bytes": len(token),
        "placeholder_network": placeholder,
        "listener_stack_bytes": int(values.get("CONFIG_RMB_LISTENER_STACK_BYTES", "0") or 0),
        "stm_device": "f103c8"
        if values.get("CONFIG_ROM_SERVICE_DEVICE_F103C8") == "y"
        else "f407zg",
    }
