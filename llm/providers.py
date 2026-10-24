"""Picks where model calls go: local Meterless Runtime if running, else NVIDIA's API (same request format)."""
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
# Checked 22 Oct: nemotron-3-super-120b-a12b is listed but returns 404; these two answer tools and JSON
NVIDIA_MODELS = ("nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3.5-super-vl-120b-a12b")
METERLESS_DEFAULT_URL = "http://127.0.0.1:8088/v1"
METERLESS_PORTS = (8088, 8090, 8787, 11434)
METERLESS_HOSTS = ("127.0.0.1", "localhost", "[::1]")
METERLESS_APP_ID = "lotline"
# Model loads on a local runtime can take minutes
METERLESS_TIMEOUT = 180


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    models: list[str]
    headers: dict = field(default_factory=dict)
    chat_headers: dict = field(default_factory=dict)
    timeout: float = 90
    extra_body: dict = field(default_factory=dict)


def _setting(*names: str, default: str = "") -> str:
    return next((os.getenv(n) for n in names if os.getenv(n)), default)


def _is_local(url: str) -> bool:
    return any(h in url for h in ("127.0.0.1", "localhost", "[::1]"))


def _normalize(url: str) -> str:
    url = url.strip().rstrip("/")
    return url if url.endswith("/v1") else url.split("/v1")[0] + "/v1"


# Meterless Runtime

_cache: dict = {"at": 0.0, "status": None}


def _token() -> str:
    token = _setting("METERLESS_RUNTIME_TOKEN", "METERLESS_API_KEY")
    if not token and _is_local(os.getenv("MODEL_BASE_URL", "")):
        token = os.getenv("MODEL_API_KEY", "")
    if not token:
        state = os.getenv("METERLESS_STATE_DIR")
        path = Path(state) / "proxy-token" if state else None
        if path and path.exists():
            token = path.read_text(encoding="utf-8").strip()
    # The runtime's placeholder token never authenticates
    return "" if token in ("", "rt-local") or token.startswith("your") else token


def _open(host: str, port: int) -> bool:
    import socket
    try:
        with socket.create_connection((host.strip("[]"), port), timeout=0.3):
            return True
    except OSError:
        return False


def _detect() -> str | None:
    """Find a running Runtime by trying every known port at once."""
    import requests
    from concurrent.futures import ThreadPoolExecutor
    pairs = [(h, p) for p in METERLESS_PORTS for h in METERLESS_HOSTS]
    with ThreadPoolExecutor(len(pairs)) as pool:
        open_ = list(pool.map(lambda hp: _open(*hp), pairs))
    for (host, port), ok in zip(pairs, open_):
        if not ok:
            continue
        try:
            if requests.get(f"http://{host}:{port}/healthz", timeout=2).ok:
                return f"http://{host}:{port}/v1"
        except requests.RequestException:
            continue
    return None


def meterless_status(refresh: bool = False) -> dict:
    """disabled / offline / unauthorized / online, decided by GET /v1/models as the runtime documents."""
    if not refresh and _cache["status"] and time.time() - _cache["at"] < 60:
        return _cache["status"]
    status = _probe()
    _cache.update(at=time.time(), status=status)
    return status


def _probe() -> dict:
    if os.getenv("METERLESS_ENABLED", "true").lower() != "true":
        return {"state": "disabled"}
    import requests
    configured = os.getenv("METERLESS_BASE_URL") or (os.getenv("MODEL_BASE_URL") if _is_local(os.getenv("MODEL_BASE_URL", "")) else "")
    url = _normalize(configured) if configured else _detect()
    if not url:
        return {"state": "offline", "detail": "no Meterless Runtime found on the usual local ports"}
    token = _token()
    headers = {"meterless-app": METERLESS_APP_ID, **({"Authorization": f"Bearer {token}"} if token else {})}
    try:
        resp = requests.get(f"{url}/models", headers=headers, timeout=5)
    except requests.RequestException as exc:
        return {"state": "offline", "base_url": url, "detail": str(exc)[:120]}
    if resp.status_code in (401, 403):
        return {"state": "unauthorized", "base_url": url, "detail": "set METERLESS_RUNTIME_TOKEN or METERLESS_STATE_DIR"}
    if resp.status_code != 200:
        return {"state": "offline", "base_url": url, "detail": f"HTTP {resp.status_code}"}
    ids = [m.get("id", "") for m in (resp.json().get("data") or []) if isinstance(m, dict)]
    ids = [i for i in ids if i.startswith("meterless/")] or ids
    if not ids:
        return {"state": "offline", "base_url": url, "detail": "runtime listed no models"}
    return {"state": "online", "base_url": url, "models": ids, "token": token}


def meterless_provider() -> Provider | None:
    status = meterless_status()
    if status["state"] != "online":
        return None
    wanted = os.getenv("METERLESS_MODEL")
    models = [wanted] if wanted else status["models"][:2]
    return Provider(name="Meterless Runtime", base_url=status["base_url"], api_key=status["token"] or "none",
                    models=models, headers={"meterless-app": METERLESS_APP_ID},
                    chat_headers={"meterless-scope": "user"}, timeout=METERLESS_TIMEOUT)


# NVIDIA API (build.nvidia.com)

def nvidia_provider() -> Provider | None:
    key = os.getenv("NVIDIA_API_KEY") or (os.getenv("MODEL_API_KEY", "") if os.getenv("MODEL_API_KEY", "").startswith("nvapi-") else "")
    if not key or key.startswith("your"):
        return None
    configured = os.getenv("MODEL_BASE_URL", "")
    primary = _setting("NVIDIA_MODEL", default="") or (os.getenv("NEMOTRON_MODEL", "") if os.getenv("NEMOTRON_MODEL", "").startswith("nvidia/") else NVIDIA_MODELS[0])
    fallback = _setting("NEMOTRON_FALLBACK_MODEL", "NVIDIA_FALLBACK_MODEL", default=NVIDIA_MODELS[1])
    thinking = os.getenv("NVIDIA_THINKING", "false").lower() == "true"
    return Provider(name="NVIDIA API", base_url=_normalize(configured) if "nvidia" in configured else NVIDIA_BASE_URL,
                    api_key=key, models=list(dict.fromkeys([primary, fallback])),
                    # Nemotron 3 reasons by default and can spend the whole token budget before answering
                    extra_body={"chat_template_kwargs": {"enable_thinking": thinking}})


def providers() -> list[Provider]:
    """In the order they are tried. MODEL_PROVIDER=nvidia puts NVIDIA first."""
    chain = [meterless_provider(), nvidia_provider()]
    if os.getenv("MODEL_PROVIDER", "meterless").lower() == "nvidia":
        chain.reverse()
    return [p for p in chain if p]
