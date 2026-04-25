"""Constants shared across dashboard modules."""

import os
import re

# Patterns to redact from public-facing output
SENSITIVE_PATTERNS = re.compile(
    r'(GEMINI_API_KEY|API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIALS|Authorization|Bearer\s+\S+)'
    r'|([A-Za-z0-9_-]{30,}(?=[\s"\']))',  # long token-like strings
    re.IGNORECASE
)


def sanitize(text):
    """Remove API keys and sensitive strings from text before serving."""
    return SENSITIVE_PATTERNS.sub('[REDACTED]', text)


PROJECT_DIR = os.path.expanduser("~/projects/kaetram-agent")
STATE_DIR = os.path.join(PROJECT_DIR, "state")
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
DATASET_DIR = os.path.join(PROJECT_DIR, "dataset")

# Multi-agent constants (must match orchestrate.py)
BASE_SERVER_PORT = 9001
PORT_STRIDE = 10
MAX_AGENTS = 3
WS_PORT = 8081
SCREENSHOT_POLL_INTERVAL = 0.25  # seconds between mtime checks (4 FPS for live stream feel)
SCREENSHOT_MAX_AGE = 60  # seconds — screenshots older than this are considered stale

# Cache TTLs (seconds) — used by api.py endpoints
AGENTS_CACHE_TTL = 15
STATS_CACHE_TTL = 30
EVAL_LIVE_CACHE_TTL = 1

# Gzip compression threshold (bytes). Smaller payloads skip gzip — overhead
# of compress/decompress exceeds wire savings on tiny JSON.
GZIP_MIN_BYTES = 4096

# Subprocess cache (shared across endpoints to avoid redundant forks)
_ss_cache = {"output": "", "time": 0}
_SS_CACHE_TTL = 5  # seconds


def get_ss_output():
    """Return cached `ss -tlnp` output (5s TTL, avoids forking on every request)."""
    import subprocess
    import time
    now = time.time()
    if now - _ss_cache["time"] < _SS_CACHE_TTL:
        return _ss_cache["output"]
    try:
        result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=3)
        _ss_cache["output"] = result.stdout
    except Exception:
        pass  # return stale cache on failure
    _ss_cache["time"] = now
    return _ss_cache["output"]


def check_process_running(pattern: str) -> bool:
    """Check if a process matching pattern is running via /proc scan (no subprocess fork)."""
    import os
    for pid_dir in os.listdir("/proc"):
        if not pid_dir.isdigit():
            continue
        try:
            with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
            if pattern in cmdline:
                return True
        except (OSError, PermissionError):
            continue
    return False


# MongoDB (Kaetram game server database)
MONGO_HOST = os.environ.get("KAETRAM_MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.environ.get("KAETRAM_MONGO_PORT", "27017"))
MONGO_DB = os.environ.get("KAETRAM_MONGO_DB", "kaetram_devlopment")
