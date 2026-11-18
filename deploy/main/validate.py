"""Check main-domain candidate inputs; never deploy or connect.

prepare: verify a disabled plan, including incomplete data/budget decisions.
preflight: additionally reject incomplete *declared* main-release prerequisites.
Neither command verifies the live server or authorizes generation.
"""

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import subprocess


EXAMPLE = Path(__file__).with_name(".env.example")
ROOT = EXAMPLE.resolve().parents[2]
FIXED = {
    "MENU_DEPLOYMENT_ENV": "production",
    "FRONTEND_ORIGINS": "https://www.me-nu.kr,https://api.me-nu.kr",
    "OAUTH_FRONTEND_ORIGIN": "https://www.me-nu.kr",
    "OAUTH_PUBLIC_API_ORIGIN": "https://api.me-nu.kr",
    "SESSION_BACKEND": "redis",
    "SESSION_COOKIE_NAME": "menu_main_session",
    "SESSION_COOKIE_SECURE": "true",
    "REDIS_KEY_PREFIX": "me_nu_main",
    "ENABLE_DEV_API": "false",
    "ENABLE_DEV_AUTH_BYPASS": "false",
    "CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED": "false",
    "MAIN_PLAN_AUTO_COLLECTION_ENABLED": "false",
    "MAIN_PLAN_PAID_GENERATION_ENABLED": "false",
    "DAILY_APPROVED_CONTENT_TARGET": "20",
    "GENERATION_TIMEZONE": "Asia/Seoul",
}
ATTESTATIONS = (
    "MAIN_BACKUP_VERIFIED", "MAIN_DATA_PARITY_VERIFIED", "MAIN_DB_CAPACITY_VERIFIED", "MAIN_DNS_TLS_VERIFIED",
    "MAIN_OAUTH_CALLBACKS_VERIFIED", "MAIN_FRONTEND_BUILD_VERIFIED", "MAIN_RELEASE_APPROVED",
)
CHOICES = ("MAIN_IMAGE_TAG", "MAIN_DATA_STRATEGY", "MAIN_TARGET_DATABASE", "MAIN_EVENT_DATA_SOURCE")
GENERATION_LIMITS = ("MAX_GENERATION_ATTEMPTS_PER_DAY", "GENERATION_DAILY_BUDGET_USD", "GENERATION_ENDS_AT")
RUNTIME_KEYS = ("MAIN_POSTGRES_PASSWORD", "MAIN_REDIS_PASSWORD", "MAIN_FORWARDED_ALLOW_IPS",
    "MAIN_KAKAO_OAUTH_ENABLED", "MAIN_KAKAO_CLIENT_ID", "MAIN_KAKAO_CLIENT_SECRET",
    "MAIN_NAVER_OAUTH_ENABLED", "MAIN_NAVER_CLIENT_ID", "MAIN_NAVER_CLIENT_SECRET",
    "MAIN_LLM_KEY", "MAIN_DICT_DATABASE_URL", "MAIN_CHAT_DAILY_TOTAL_LIMIT")
KEYS = set(FIXED) | set(ATTESTATIONS) | set(CHOICES) | set(GENERATION_LIMITS) | set(RUNTIME_KEYS)
# Chatbot dictionary: the read-only menu_dict database inside the shared Checking PostgreSQL (same as Checking).
DICT_URL = re.compile(r"postgresql\+psycopg://menu_dict_reader:([0-9a-f]{64})@postgres:5432/menu_dict")
# All users' chatbot questions per day: an explicit 1-999999 so a typo can never mean "unlimited" (0).
DAILY_LIMIT = re.compile(r"[1-9][0-9]{0,5}")


class GuardError(ValueError):
    """Errors contain setting names only, never supplied values."""


def read_manifest(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise GuardError("Could not read the preparation manifest") from None
    values = {}
    for line in lines:
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in KEYS or key in values:
            raise GuardError("Manifest has an unknown, duplicate or malformed setting")
        if value != value.strip() or any(char in value for char in ("'", '"', "`", "$", "\\", "#")):
            raise GuardError(f"{key} requires a plain literal value")
        values[key] = value
    return values


def validate(values: dict[str, str]) -> dict:
    if set(values) != KEYS:
        raise GuardError("Manifest setting names must match .env.example exactly")
    for key, expected in FIXED.items():
        if values[key] != expected:
            raise GuardError(f"{key} does not match the disabled main preparation plan")
    for key in ATTESTATIONS:
        if values[key] not in {"true", "false"}:
            raise GuardError(f"{key} requires true or false")
    if values["MAIN_IMAGE_TAG"] and not re.fullmatch(r"[0-9a-f]{40}", values["MAIN_IMAGE_TAG"]):
        raise GuardError("MAIN_IMAGE_TAG requires a full Git commit")
    strategy = values["MAIN_DATA_STRATEGY"]
    if strategy != "reuse_checking":
        raise GuardError("MAIN_DATA_STRATEGY must preserve the approved existing Checking data")
    database = values["MAIN_TARGET_DATABASE"]
    if database != "me_nu_checking":
        raise GuardError("MAIN_TARGET_DATABASE does not match reuse_checking")
    if values["MAIN_EVENT_DATA_SOURCE"] != "local_demo":
        raise GuardError("MAIN_EVENT_DATA_SOURCE must preserve the current event provenance")
    for key in ("MAIN_POSTGRES_PASSWORD", "MAIN_REDIS_PASSWORD"):
        if values[key] and not re.fullmatch(r"[0-9a-f]{64}", values[key]):
            raise GuardError(f"{key} must reuse a valid existing Checking credential")
    if values["MAIN_POSTGRES_PASSWORD"] and values["MAIN_POSTGRES_PASSWORD"] == values["MAIN_REDIS_PASSWORD"]:
        raise GuardError("Existing PostgreSQL and Redis credentials must be distinct")
    if re.search(r"\s", values["MAIN_LLM_KEY"]):
        raise GuardError("MAIN_LLM_KEY must be empty or a single token")
    if dictionary := values["MAIN_DICT_DATABASE_URL"]:
        match = DICT_URL.fullmatch(dictionary)
        stores = {values["MAIN_POSTGRES_PASSWORD"], values["MAIN_REDIS_PASSWORD"]} - {""}
        if not match or len(set(match[1])) < 8 or match[1] in stores:
            raise GuardError("MAIN_DICT_DATABASE_URL must be empty or the read-only menu_dict_reader on the shared "
                             "Checking PostgreSQL with its own random 64-character hex password")
    if not DAILY_LIMIT.fullmatch(values["MAIN_CHAT_DAILY_TOTAL_LIMIT"]):
        raise GuardError("MAIN_CHAT_DAILY_TOTAL_LIMIT must be a whole number from 1 to 999999 "
                         "(all users' chatbot questions per day, shared with Checking)")
    try:
        address = ip_address(values["MAIN_FORWARDED_ALLOW_IPS"])
        if address.is_unspecified:
            raise ValueError
    except ValueError:
        raise GuardError("MAIN_FORWARDED_ALLOW_IPS requires one explicit proxy IP") from None
    runtime_pending = [key for key in ("MAIN_POSTGRES_PASSWORD", "MAIN_REDIS_PASSWORD") if not values[key]]
    enabled = []
    for provider in ("KAKAO", "NAVER"):
        flag = f"MAIN_{provider}_OAUTH_ENABLED"
        if values[flag] not in {"true", "false"}:
            raise GuardError(f"{flag} requires true or false")
        if values[flag] == "true":
            enabled.append(provider)
            runtime_pending.extend(key for key in (f"MAIN_{provider}_CLIENT_ID", f"MAIN_{provider}_CLIENT_SECRET") if not values[key])
    if not enabled:
        runtime_pending.append("MAIN_OAUTH_PROVIDER_ENABLED")
    attempts = values["MAX_GENERATION_ATTEMPTS_PER_DAY"]
    if attempts and not re.fullmatch(r"[1-9][0-9]*", attempts):
        raise GuardError("MAX_GENERATION_ATTEMPTS_PER_DAY requires an independent positive attempt limit")
    budget = values["GENERATION_DAILY_BUDGET_USD"]
    if budget:
        try:
            amount = Decimal(budget)
        except InvalidOperation:
            raise GuardError("GENERATION_DAILY_BUDGET_USD requires a positive finite amount") from None
        if not amount.is_finite() or amount <= 0:
            raise GuardError("GENERATION_DAILY_BUDGET_USD requires a positive finite amount")
    ends_at = values["GENERATION_ENDS_AT"]
    if ends_at:
        try:
            parsed = datetime.fromisoformat(ends_at)
            if parsed.utcoffset() is None:
                raise ValueError
        except ValueError:
            raise GuardError("GENERATION_ENDS_AT requires an explicit ISO 8601 timezone") from None

    return {
        "preparation_valid": True,
        "live_checks_performed": False,
        "deployment_performed": False,
        "compose_config_verified": False,
        "generation_authorized": False,
        "daily_approved_content_target": 20,
        "release_blockers": [key for key in CHOICES if not values[key]]
            + [key for key in ATTESTATIONS if values[key] != "true"] + runtime_pending,
        "generation_decisions_pending": [key for key in GENERATION_LIMITS if not values[key]],
        "daily_scheduler_implemented": False,
        # Chatbot settings are optional; report only whether they are set, never their values.
        "chatbot_llm_key": "set" if values["MAIN_LLM_KEY"] else "empty",
        "chatbot_dictionary": "set" if values["MAIN_DICT_DATABASE_URL"] else "empty",
        "chatbot_daily_total_limit": int(values["MAIN_CHAT_DAILY_TOTAL_LIMIT"]),
    }


def validate_config(config: dict, values: dict[str, str]) -> None:
    """Check offline Compose JSON without exposing its credential-bearing values."""
    validate(values)
    def require(condition, message):
        if not condition:
            raise GuardError(message)
    require(config.get("name") == "me-nu-main", "Unexpected main project")
    require(set(config.get("services", {})) == {"api"}, "Main candidate must contain only the API")
    require(not any(config.get(key) for key in ("volumes", "secrets", "configs")), "Main candidate must not create data or key mounts")
    networks = config.get("networks", {})
    require(set(networks) == {"data", "egress"}, "Unexpected main networks")
    normalized_networks = {name: {key: value for key, value in network.items() if not (key == "ipam" and value == {})}
                           for name, network in networks.items()}
    require(normalized_networks["data"] == {"name": "me-nu-checking_data", "external": True}, "Main must reuse the existing Checking network")
    require(normalized_networks["egress"] == {"name": "me-nu-main_egress"}, "Unexpected main egress network")
    api = config["services"]["api"]
    require(api.get("image") == f'me-nu-backend-main:{values["MAIN_IMAGE_TAG"]}', "Main image must use the reviewed commit")
    require(not any(api.get(key) for key in ("build", "volumes", "secrets", "configs", "depends_on", "command", "entrypoint", "privileged", "network_mode", "container_name")), "Main candidate must not override image execution or add dependencies/mounts")
    require(api.get("ports") == [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 8000, "published": "8003", "protocol": "tcp"}], "Main API must bind only loopback port 8003")
    require(set(api.get("networks", {})) == {"data", "egress"}, "Unexpected main API networks")
    require(api.get("read_only") and api.get("init") and api.get("cap_drop") == ["ALL"]
        and api.get("security_opt") == ["no-new-privileges:true"], "Main API sandbox settings changed")
    require(str(api.get("mem_limit")) == str(512 * 1024**2) and float(api.get("cpus", 0)) == 0.5
        and api.get("pids_limit") == 128 and api.get("tmpfs") == ["/tmp:rw,noexec,nosuid,size=64m"], "Main API resource bounds changed")
    environment = {key: value for key, value in FIXED.items() if key not in {
        "MAIN_PLAN_AUTO_COLLECTION_ENABLED", "MAIN_PLAN_PAID_GENERATION_ENABLED", "DAILY_APPROVED_CONTENT_TARGET", "GENERATION_TIMEZONE"}}
    environment.update({
        "DATABASE_URL": f'postgresql+psycopg://me_nu_checking:{values["MAIN_POSTGRES_PASSWORD"]}@postgres:5432/me_nu_checking',
        "DATABASE_ECHO": "false", "DATABASE_POOL_SIZE": "2", "DATABASE_MAX_OVERFLOW": "0",
        "REDIS_URL": f'redis://:{values["MAIN_REDIS_PASSWORD"]}@redis:6379/0',
        "REDIS_SOCKET_TIMEOUT_SECONDS": "0.5", "REDIS_MAX_CONNECTIONS": "10",
        "SESSION_EXPIRE_MINUTES": "30", "FORWARDED_ALLOW_IPS": values["MAIN_FORWARDED_ALLOW_IPS"],
        "OAUTH_STATE_TTL_SECONDS": "600", "EVENT_DATA_SOURCE": "local_demo",
        "TMDB_KEY": "", "OPENAI_API_KEY": "",
        # Only the chatbot key and dictionary come from the server file; empty keeps the chatbot off.
        "LLM_KEY": values["MAIN_LLM_KEY"], "DICT_DATABASE_URL": values["MAIN_DICT_DATABASE_URL"],
        "CHAT_DAILY_TOTAL_LIMIT": values["MAIN_CHAT_DAILY_TOTAL_LIMIT"],
    })
    for provider in ("KAKAO", "NAVER"):
        for suffix in ("OAUTH_ENABLED", "CLIENT_ID", "CLIENT_SECRET"):
            environment[f"{provider}_{suffix}"] = values[f"MAIN_{provider}_{suffix}"]
    require(api.get("environment") == environment, "Main API environment does not match the reviewed data/auth/chatbot-key plan")


def check_compose(path: Path, values: dict[str, str]) -> None:
    """Render locally, removing inherited overrides; retain all secrets in memory."""
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() not in KEYS and not key.upper().startswith(("MAIN_", "COMPOSE_"))}
    try:
        result = subprocess.run(["docker", "--context", "default", "compose", "--env-file", str(path.resolve()),
            "-p", "me-nu-main", "-f", str(ROOT / "compose.main.yaml"), "config", "--format", "json"],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise GuardError("Offline Compose rendering failed; credential-bearing output suppressed")
        config = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise GuardError("Offline Compose check could not complete; credential-bearing output suppressed") from None
    validate_config(config, values)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "preflight"))
    parser.add_argument("--env", type=Path, default=EXAMPLE)
    parser.add_argument("--check-compose", action="store_true", help="Check offline Compose rendering; never start containers")
    args = parser.parse_args(argv)
    try:
        values = read_manifest(args.env)
        report = validate(values)
        if args.check_compose or (args.mode == "preflight" and not report["release_blockers"]):
            check_compose(args.env, values)
            report["compose_config_verified"] = True
    except GuardError as error:
        print(json.dumps({"preparation_valid": False, "error": str(error)}))
        return 2
    report["mode"] = args.mode
    print(json.dumps(report, indent=2))
    return 2 if args.mode == "preflight" and report["release_blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
