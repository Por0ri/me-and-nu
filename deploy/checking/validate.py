"""Validate the isolated checking stack without printing passwords or resolved config."""
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
ENV_FILE = ROOT / "deploy/checking/.env"
KEYS = {
    "CHECKING_IMAGE_TAG", "CHECKING_POSTGRES_PASSWORD", "CHECKING_REDIS_PASSWORD", "CHECKING_FORWARDED_ALLOW_IPS",
    "CHECKING_KAKAO_OAUTH_ENABLED", "CHECKING_KAKAO_CLIENT_ID", "CHECKING_KAKAO_CLIENT_SECRET",
    "CHECKING_NAVER_OAUTH_ENABLED", "CHECKING_NAVER_CLIENT_ID", "CHECKING_NAVER_CLIENT_SECRET",
}
FIXED_ENV = {
    "MENU_DEPLOYMENT_ENV": "checking", "SESSION_BACKEND": "redis", "REDIS_KEY_PREFIX": "me_nu_checking",
    "SESSION_COOKIE_NAME": "menu_checking_session", "SESSION_COOKIE_SECURE": "true",
    "ENABLE_DEV_API": "false", "ENABLE_DEV_AUTH_BYPASS": "false", "DATABASE_ECHO": "false",
    "FRONTEND_ORIGINS": "https://checking.me-nu.kr,https://api.checking.me-nu.kr",
    "OAUTH_FRONTEND_ORIGIN": "https://checking.me-nu.kr", "OAUTH_PUBLIC_API_ORIGIN": "https://api.checking.me-nu.kr",
    "LLM_KEY": "", "TMDB_KEY": "", "OPENAI_API_KEY": "",
}


class GuardError(ValueError):
    pass


def validate_values(values, environ=None):
    if set(values) != KEYS:
        raise GuardError("The checking environment must contain exactly the keys in deploy/checking/.env.example.")
    for key, value in values.items():
        if not isinstance(value, str) or re.search(r"[\s\"'`$#\\]", value):
            raise GuardError(f"Use a single unquoted value for {key}; whitespace and shell substitutions are not supported.")
        if environ is not None and key in environ and environ[key] != value:
            raise GuardError(f"An inherited {key} would override the checking file; unset it before continuing.")
    if not re.fullmatch(r"[0-9a-f]{40}", values["CHECKING_IMAGE_TAG"]):
        raise GuardError("CHECKING_IMAGE_TAG must identify the exact 40-character lowercase Git commit.")
    passwords = [values[key] for key in ("CHECKING_POSTGRES_PASSWORD", "CHECKING_REDIS_PASSWORD")]
    if any(not re.fullmatch(r"[0-9a-f]{64}", value) or len(set(value)) < 8 for value in passwords) or passwords[0] == passwords[1]:
        raise GuardError("Generate two different random 64-character hex passwords for the checking stores.")
    try:
        addresses = values["CHECKING_FORWARDED_ALLOW_IPS"].split(",")
        if not addresses or any(ipaddress.ip_address(value).is_unspecified for value in addresses):
            raise ValueError()
    except ValueError:
        raise GuardError("CHECKING_FORWARDED_ALLOW_IPS must list exact trusted IP addresses; wildcards are forbidden.") from None
    for provider in ("KAKAO", "NAVER"):
        enabled = values[f"CHECKING_{provider}_OAUTH_ENABLED"]
        if enabled not in ("true", "false"):
            raise GuardError(f"CHECKING_{provider}_OAUTH_ENABLED must be true or false.")
        if enabled == "true" and not all(values[f"CHECKING_{provider}_{field}"] for field in ("CLIENT_ID", "CLIENT_SECRET")):
            raise GuardError(f"The enabled {provider} provider needs both server credentials.")
    return values


def read_environment(path=ENV_FILE, environ=None):
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise GuardError("The checking environment file must have permissions 0600.")
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
    if config.get("name") != "me-nu-checking":
        raise GuardError("Unexpected Compose project; only me-nu-checking is allowed.")
    services = config.get("services", {})
    if set(services) != {"postgres", "redis", "migrate", "api"}:
        raise GuardError("Unexpected checking service set.")
    if set(config.get("volumes", {})) != {"postgres-data", "redis-data"} or set(config.get("networks", {})) != {"data", "egress"}:
        raise GuardError("Unexpected checking volumes or networks.")
    for name in ("postgres-data", "redis-data"):
        volume = config.get("volumes", {}).get(name, {})
        if volume.get("name") != f"me-nu-checking_{name}" or volume.get("external") or volume.get("driver_opts"):
            raise GuardError("Checking volumes must be private named volumes owned by me-nu-checking.")
    for name in ("data", "egress"):
        network = config.get("networks", {}).get(name, {})
        if network.get("name") != f"me-nu-checking_{name}" or network.get("external"):
            raise GuardError("Checking networks must be owned by me-nu-checking.")
    if not config["networks"]["data"].get("internal"):
        raise GuardError("Checking data network must be internal.")
    expected_db = f"postgresql+psycopg://me_nu_checking:{values['CHECKING_POSTGRES_PASSWORD']}@postgres:5432/me_nu_checking"
    expected_redis = f"redis://:{values['CHECKING_REDIS_PASSWORD']}@redis:6379/0"
    for name in ("api", "migrate"):
        service = services[name]
        environment = service.get("environment", {})
        if any(str(environment.get(key)) != value for key, value in FIXED_ENV.items()):
            raise GuardError(f"{name} has an unsafe origin, session, or deployment setting.")
        if environment.get("DATABASE_URL") != expected_db or environment.get("REDIS_URL") != expected_redis:
            raise GuardError(f"{name} must use the private checking database and Redis.")
        if service.get("image") != f"me-nu-backend-checking:{values['CHECKING_IMAGE_TAG']}":
            raise GuardError("The backend image must use the dedicated checking repository and commit tag.")
        build = service.get("build", {})
        if Path(build.get("context", "")).resolve() != (ROOT / "backend").resolve() or build.get("dockerfile", "Dockerfile") != "Dockerfile":
            raise GuardError("Checking must build only the reviewed backend directory and Dockerfile.")
        if environment.get("FORWARDED_ALLOW_IPS") != values["CHECKING_FORWARDED_ALLOW_IPS"]:
            raise GuardError("The proxy trust list must match the validated Checking environment.")
        for provider in ("KAKAO", "NAVER"):
            for field in ("OAUTH_ENABLED", "CLIENT_ID", "CLIENT_SECRET"):
                if environment.get(f"{provider}_{field}") != values[f"CHECKING_{provider}_{field}"]:
                    raise GuardError("OAuth configuration must match the dedicated Checking environment.")
        if not service.get("read_only") or "ALL" not in service.get("cap_drop", []):
            raise GuardError("Backend containers must retain read-only filesystems and dropped capabilities.")
        if "no-new-privileges:true" not in service.get("security_opt", []) or not service.get("init"):
            raise GuardError("Backend containers must retain process and privilege isolation.")
        if service.get("entrypoint"):
            raise GuardError("Checking backend containers cannot override the reviewed entrypoint.")
    if services["migrate"].get("command") != ["python", "-m", "alembic", "upgrade", "150a601074af"]:
        raise GuardError("Checking migration must target only 150a601074af; head/heads and DOB removal are forbidden.")
    if services["api"].get("command"):
        raise GuardError("Checking API must retain the reviewed Dockerfile command.")
    dependencies = services["api"].get("depends_on", {})
    if any(dependencies.get(name, {}).get("condition") != condition for name, condition in {
        "postgres": "service_healthy", "redis": "service_healthy", "migrate": "service_completed_successfully",
    }.items()):
        raise GuardError("Checking API startup must wait for healthy stores and successful migration.")
    for name, (maximum, cpus) in {"api": (512, .5), "postgres": (512, .35), "redis": (128, .15), "migrate": (256, .25)}.items():
        service = services[name]
        if not 0 < int(service.get("mem_limit", 0)) <= maximum * 1024 * 1024 or not 0 < float(service.get("cpus", 0)) <= cpus:
            raise GuardError(f"{name} is missing its bounded CPU/memory limits.")
        if set(service.get("networks", {})) != ({"data", "egress"} if name == "api" else {"data"}):
            raise GuardError("Only the checking API may use the dedicated egress network.")
        if service.get("container_name") or service.get("privileged") or service.get("network_mode") or service.get("volumes_from"):
            raise GuardError("Checking services cannot reuse external containers or privileged/host networking.")
        if name != "api" and service.get("ports"):
            raise GuardError("Only the checking API may publish a host port.")
    ports = services["api"].get("ports", [])
    if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1" or str(ports[0].get("published")) != "8002" or ports[0].get("target") != 8000 or ports[0].get("protocol", "tcp") != "tcp":
        raise GuardError("The checking API must publish only 127.0.0.1:8002.")
    for name, volume, target in (("postgres", "postgres-data", "/var/lib/postgresql/data"), ("redis", "redis-data", "/data")):
        mounts = services[name].get("volumes", [])
        if len(mounts) != 1 or mounts[0].get("type") != "volume" or mounts[0].get("source") != volume or mounts[0].get("target") != target:
            raise GuardError("Database mounts must use the dedicated checking volumes.")
    if services["api"].get("volumes") or services["migrate"].get("volumes"):
        raise GuardError("API and migration containers must not mount host or staging data.")
    pg_env = services["postgres"].get("environment", {})
    if pg_env.get("POSTGRES_DB") != "me_nu_checking" or pg_env.get("POSTGRES_USER") != "me_nu_checking" or pg_env.get("POSTGRES_PASSWORD") != values["CHECKING_POSTGRES_PASSWORD"]:
        raise GuardError("PostgreSQL must use the dedicated checking database and account.")
    if services["postgres"].get("command") != ["postgres", "-c", "shared_buffers=128MB", "-c", "max_connections=30"]:
        raise GuardError("PostgreSQL must retain the bounded buffer and connection settings.")
    redis = services["redis"]
    if redis.get("environment", {}).get("REDISCLI_AUTH") != values["CHECKING_REDIS_PASSWORD"] or redis.get("command") != ["redis-server", "--requirepass", values["CHECKING_REDIS_PASSWORD"], "--appendonly", "yes", "--appendfsync", "always", "--maxmemory", "48mb", "--maxmemory-policy", "noeviction"]:
        raise GuardError("Redis must retain its separate password and bounded persistent store.")


def validate_build_source(values):
    """Bind the image tag to this checkout before Compose builds backend/."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    # The documented sudo helper may read a checkout owned by the deploy user.
    # Trust only this explicit checkout for these read-only calls, never globally.
    command = ["git", "--no-optional-locks", "-c", f"safe.directory={ROOT}", "-C", str(ROOT)]
    options = dict(cwd=ROOT, env=environment, capture_output=True, text=True, check=False, timeout=10)
    head = subprocess.run([*command, "rev-parse", "--verify", "HEAD"], **options)
    if head.returncode or head.stdout.strip() != values["CHECKING_IMAGE_TAG"]:
        raise GuardError("Checking build requires CHECKING_IMAGE_TAG to match this checkout's exact Git HEAD.")
    status = subprocess.run([*command, "status", "--porcelain=v1", "--untracked-files=all", "--", "backend"], **options)
    if status.returncode or status.stdout.strip():
        raise GuardError("Checking build requires a clean backend directory, including staged and untracked files.")


def check(*, build=False):
    values = read_environment(environ=os.environ)
    if build:
        validate_build_source(values)
    environment = dict(os.environ)
    for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
        environment.pop(key, None)
    result = subprocess.run([
        "docker", "--context", "default", "compose", "--env-file", str(ENV_FILE),
        "-p", "me-nu-checking", "-f", str(ROOT / "compose.checking.yaml"), "config", "--format", "json",
    ], cwd=ROOT, env=environment, capture_output=True, text=True, check=False, timeout=30)
    if result.returncode:
        raise GuardError("Compose validation failed; raw output is suppressed to protect environment values.")
    validate_config(json.loads(result.stdout), values)
    print("Checking configuration valid: isolated project/volumes/networks, loopback 8002, fixed checking origins, bounded resources.")


def initialize(image_tag):
    if not re.fullmatch(r"[0-9a-f]{40}", image_tag):
        raise GuardError("Use the full lowercase Git commit as --image-tag.")
    template = (ROOT / "deploy/checking/.env.example").read_text(encoding="utf-8")
    for key, value in {"CHECKING_IMAGE_TAG": image_tag, "CHECKING_POSTGRES_PASSWORD": secrets.token_hex(32), "CHECKING_REDIS_PASSWORD": secrets.token_hex(32)}.items():
        template = template.replace(f"{key}=\n", f"{key}={value}\n")
    descriptor = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(template)
    print("Created deploy/checking/.env with separate random store passwords; OAuth remains disabled.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "check", "check-build"))
    parser.add_argument("--image-tag")
    args = parser.parse_args()
    try:
        if args.action == "init":
            initialize(args.image_tag or "")
        else:
            check(build=args.action == "check-build")
    except GuardError as error:
        parser.exit(1, f"Checking guard: {error}\n")
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        parser.exit(1, "Checking guard: file/configuration unavailable or invalid; no environment values printed.\n")


if __name__ == "__main__":
    main()
