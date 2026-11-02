"""Local authenticated, read-only monitoring. Credentials come from worker/.env."""
import os

credential = os.environ.get("FLOWER_BASIC_AUTH", "")
username, separator, password = credential.partition(":")
if not separator or not username or not password or "," in credential:
    raise ValueError("FLOWER_BASIC_AUTH requires one nonempty username:password pair")
basic_auth = [credential]
address = "0.0.0.0"
port = 5555
read_only = True
persistent = True
db = "/var/lib/menu-flower/flower"
state_save_interval = 10000
max_tasks = 2000
max_workers = 10
enable_events = True
