"""Validate the isolated labs stack without printing passwords or resolved config."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "deploy/labs/.env"
KEYS = {
    "LABS_IMAGE_TAG", "LABS_POSTGRES_PASSWORD", "LABS_REDIS_PASSWORD", "LABS_FORWARDED_ALLOW_IPS",
    "LABS_KAKAO_OAUTH_ENABLED", "LABS_KAKAO_CLIENT_ID", "LABS_KAKAO_CLIENT_SECRET",
    "LABS_NAVER_OAUTH_ENABLED", "LABS_NAVER_CLIENT_ID", "LABS_NAVER_CLIENT_SECRET",
}
FIXED_ENV = {
    "MENU_DEPLOYMENT_ENV": "labs", "SESSION_BACKEND": "redis", "REDIS_KEY_PREFIX": "me_nu_labs",
    "SESSION_COOKIE_NAME": "menu_labs_session", "SESSION_COOKIE_SECURE": "true",
    "ENABLE_DEV_API": "false", "ENABLE_DEV_AUTH_BYPASS": "false", "DATABASE_ECHO": "false",
    "FRONTEND_ORIGINS": "https://labs.me-nu.kr,https://api.labs.me-nu.kr",
    "OAUTH_FRONTEND_ORIGIN": "https://labs.me-nu.kr", "OAUTH_PUBLIC_API_ORIGIN": "https://api.labs.me-nu.kr",
}


class GuardError(ValueError):
    pass


def validate_values(values, environ=None):
    if set(values) != KEYS:
        raise GuardError("The labs environment must contain exactly the keys in deploy/labs/.env.example.")
    for key, value in values.items():
        if not isinstance(value, str) or re.search(r"[\s\"'`$#\\]", value):
            raise GuardError(f"Use a single unquoted value for {key}; whitespace and shell substitutions are not supported.")
        if environ is not None and key in environ and environ[key] != value:
            raise GuardError(f"An inherited {key} would override the labs file; unset it before continuing.")
    if not re.fullmatch(r"[0-9a-f]{40}", values["LABS_IMAGE_TAG"]):
        raise GuardError("LABS_IMAGE_TAG must identify the exact 40-character lowercase Git commit.")
    passwords = [values[key] for key in ("LABS_POSTGRES_PASSWORD", "LABS_REDIS_PASSWORD")]
    if any(not re.fullmatch(r"[0-9a-f]{64}", value) or len(set(value)) < 8 for value in passwords) or passwords[0] == passwords[1]:
        raise GuardError("Generate two different random 64-character hex passwords for the labs stores.")
    try:
        addresses = values["LABS_FORWARDED_ALLOW_IPS"].split(",")
        if not addresses or any(ipaddress.ip_address(value).is_unspecified for value in addresses):
            raise ValueError()
    except ValueError:
        raise GuardError("LABS_FORWARDED_ALLOW_IPS must list exact trusted IP addresses; wildcards are forbidden.") from None
    for provider in ("KAKAO", "NAVER"):
        enabled = values[f"LABS_{provider}_OAUTH_ENABLED"]
        if enabled not in ("true", "false"):
            raise GuardError(f"LABS_{provider}_OAUTH_ENABLED must be true or false.")
        if enabled == "true" and not all(values[f"LABS_{provider}_{field}"] for field in ("CLIENT_ID", "CLIENT_SECRET")):
            raise GuardError(f"The enabled {provider} provider needs both server credentials.")
    return values


def read_environment(path=ENV_FILE, environ=None):
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise GuardError("The labs environment file must have permissions 0600.")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise GuardError("Invalid or duplicated environment assignment; no values were printed.")
        values[key] = value
    return validate_values(values, environ)


def validate_config(config, values):
    if config.get("name") != "me-nu-labs":
        raise GuardError("Unexpected Compose project; only me-nu-labs is allowed.")
    services = config.get("services", {})
    if set(services) != {"postgres", "redis", "migrate", "api", "seed"}:
        raise GuardError("Unexpected labs service set.")
    if set(config.get("volumes", {})) != {"postgres-data", "redis-data"} or set(config.get("networks", {})) != {"data", "egress"}:
        raise GuardError("Unexpected labs volumes or networks.")
    for name in ("postgres-data", "redis-data"):
        volume = config.get("volumes", {}).get(name, {})
        if volume.get("name") != f"me-nu-labs_{name}" or volume.get("external") or volume.get("driver_opts"):
            raise GuardError("Labs volumes must be private named volumes owned by me-nu-labs.")
    for name in ("data", "egress"):
        network = config.get("networks", {}).get(name, {})
        if network.get("name") != f"me-nu-labs_{name}" or network.get("external"):
            raise GuardError("Labs networks must be owned by me-nu-labs.")
    if not config["networks"]["data"].get("internal"):
        raise GuardError("Labs data network must be internal.")
    expected_db = f"postgresql+psycopg://me_nu_labs:{values['LABS_POSTGRES_PASSWORD']}@postgres:5432/me_nu_labs"
    expected_redis = f"redis://:{values['LABS_REDIS_PASSWORD']}@redis:6379/0"
    for name in ("api", "migrate", "seed"):
        service = services[name]
        environment = service.get("environment", {})
        if any(str(environment.get(key)) != value for key, value in FIXED_ENV.items()):
            raise GuardError(f"{name} has an unsafe origin, session, or deployment setting.")
        if environment.get("DATABASE_URL") != expected_db or environment.get("REDIS_URL") != expected_redis:
            raise GuardError(f"{name} must use the private labs database and Redis.")
        if service.get("image") != f"me-nu-backend-labs:{values['LABS_IMAGE_TAG']}":
            raise GuardError("The backend image must use the dedicated labs repository and commit tag.")
        if not service.get("read_only") or "ALL" not in service.get("cap_drop", []):
            raise GuardError("Backend containers must retain read-only filesystems and dropped capabilities.")
    for name, (maximum, cpus) in {"api": (768, .75), "postgres": (768, .5), "redis": (192, .25), "migrate": (256, .25), "seed": (256, .25)}.items():
        service = services[name]
        if not 0 < int(service.get("mem_limit", 0)) <= maximum * 1024 * 1024 or not 0 < float(service.get("cpus", 0)) <= cpus:
            raise GuardError(f"{name} is missing its bounded CPU/memory limits.")
        if set(service.get("networks", {})) != ({"data", "egress"} if name == "api" else {"data"}):
            raise GuardError("Only the labs API may use the dedicated egress network.")
        if service.get("container_name") or service.get("privileged") or service.get("network_mode") or service.get("volumes_from"):
            raise GuardError("Labs services cannot reuse external containers or privileged/host networking.")
        if name != "api" and service.get("ports"):
            raise GuardError("Only the labs API may publish a host port.")
    ports = services["api"].get("ports", [])
    if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1" or str(ports[0].get("published")) != "8001" or ports[0].get("target") != 8000:
        raise GuardError("The labs API must publish only 127.0.0.1:8001.")
    for name, volume, target in (("postgres", "postgres-data", "/var/lib/postgresql/data"), ("redis", "redis-data", "/data")):
        mounts = services[name].get("volumes", [])
        if len(mounts) != 1 or mounts[0].get("type") != "volume" or mounts[0].get("source") != volume or mounts[0].get("target") != target:
            raise GuardError("Database mounts must use the dedicated labs volumes.")
    if services["api"].get("volumes") or services["migrate"].get("volumes"):
        raise GuardError("API and migration containers must not mount host or staging data.")
    seed_mounts = services["seed"].get("volumes", [])
    if len(seed_mounts) != 1 or seed_mounts[0].get("type") != "bind" or not seed_mounts[0].get("read_only") or seed_mounts[0].get("target") != "/opt/labs/seed.py" or Path(seed_mounts[0].get("source", "")).resolve() != (ROOT / "deploy/labs/seed.py").resolve():
        raise GuardError("The seed may mount only the reviewed labs seed script read-only.")
    if services["seed"].get("profiles") != ["tools"]:
        raise GuardError("Seed must remain opt-in, outside normal startup.")
    pg_env = services["postgres"].get("environment", {})
    if pg_env.get("POSTGRES_DB") != "me_nu_labs" or pg_env.get("POSTGRES_USER") != "me_nu_labs" or pg_env.get("POSTGRES_PASSWORD") != values["LABS_POSTGRES_PASSWORD"]:
        raise GuardError("PostgreSQL must use the dedicated labs database and account.")
    redis = services["redis"]
    if redis.get("environment", {}).get("REDISCLI_AUTH") != values["LABS_REDIS_PASSWORD"] or redis.get("command") != ["redis-server", "--requirepass", values["LABS_REDIS_PASSWORD"], "--appendonly", "yes", "--appendfsync", "always", "--maxmemory", "64mb", "--maxmemory-policy", "noeviction"]:
        raise GuardError("Redis must retain its separate password and bounded persistent store.")


def check():
    values = read_environment(environ=os.environ)
    environment = dict(os.environ)
    for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
        environment.pop(key, None)
    result = subprocess.run([
        "docker", "--context", "default", "compose", "--env-file", str(ENV_FILE),
        "-p", "me-nu-labs", "-f", str(ROOT / "compose.labs.yaml"), "--profile", "tools", "config", "--format", "json",
    ], cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
    if result.returncode:
        raise GuardError("Compose validation failed; raw output is suppressed to protect environment values.")
    validate_config(json.loads(result.stdout), values)
    print("Labs configuration valid: isolated project/volumes/networks, loopback 8001, fixed labs origins, bounded resources.")


def initialize(image_tag):
    if not re.fullmatch(r"[0-9a-f]{40}", image_tag):
        raise GuardError("Use the full lowercase Git commit as --image-tag.")
    template = (ROOT / "deploy/labs/.env.example").read_text(encoding="utf-8")
    for key, value in {"LABS_IMAGE_TAG": image_tag, "LABS_POSTGRES_PASSWORD": secrets.token_hex(32), "LABS_REDIS_PASSWORD": secrets.token_hex(32)}.items():
        template = template.replace(f"{key}=\n", f"{key}={value}\n")
    descriptor = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(template)
    print("Created deploy/labs/.env with separate random store passwords; OAuth remains disabled.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "check"))
    parser.add_argument("--image-tag")
    args = parser.parse_args()
    try:
        if args.action == "init":
            initialize(args.image_tag or "")
        else:
            check()
    except GuardError as error:
        parser.exit(1, f"Labs guard: {error}\n")
    except (OSError, ValueError, TypeError, KeyError):
        parser.exit(1, "Labs guard: file/configuration unavailable or invalid; no environment values printed.\n")


if __name__ == "__main__":
    main()
