"""Checks that the data layer and model providers answer."""
import os
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()
results = []


def check(name, fn):
    try:
        detail = fn() or ""
        results.append(("PASS", name, detail))
    except Exception as error:
        results.append(("FAIL", name, str(error)[:150]))


def need(*names):
    for name in names:
        assert os.environ.get(name), f"{name} is missing from .env"


def zetaris_web():
    url = os.environ.get("ZETARIS_BASE_URL", "http://localhost:3000")
    requests.get(url, timeout=10).raise_for_status()


def zetaris_sql():
    need("ZETARIS_REST_URL", "ZETARIS_ORG_ID", "ZETARIS_API_KEY")
    response = requests.post(
        os.environ["ZETARIS_REST_URL"].rstrip("/") + "/sql-editor/sqls/run",
        headers={
            "Authorization": "Bearer " + os.environ["ZETARIS_API_KEY"],
            "X-Org-ID": os.environ["ZETARIS_ORG_ID"],
            "X-Request-ID": str(uuid.uuid4()),
        },
        json={"queryId": str(uuid.uuid4()), "sql": "SELECT 1", "source": "SqlEditor", "limit": 1},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json().get("data")
    assert data and str(data[0][0]) == "1", "unexpected reply: " + response.text[:80]


def lotline_tables():
    from zetaris.client import default_source_map, backend_name
    roles = default_source_map()
    missing = [r for r in ("stock", "shipments", "contacts", "notices", "notice_lines") if r not in roles]
    assert not missing, f"no table for {', '.join(missing)}"
    return f"{backend_name()}: " + ", ".join(f"{r}={v['table']}" for r, v in sorted(roles.items()))


def _reply(provider):
    from llm.client import _create
    reply = _create(provider, provider.models[0], messages=[{"role": "user", "content": "Reply with the single word: ready"}],
                    max_tokens=20)
    assert reply.choices, "no reply from the model"
    return f"{provider.models[0]} at {provider.base_url}"


def meterless_runtime():
    from llm.providers import meterless_status, meterless_provider
    status = meterless_status(refresh=True)
    assert status["state"] == "online", f"{status['state']}: {status.get('detail', '')}"
    return _reply(meterless_provider())


def nvidia_api():
    from llm.providers import nvidia_provider
    provider = nvidia_provider()
    assert provider, "NVIDIA_API_KEY is missing from .env"
    return _reply(provider)


if os.getenv("DATA_BACKEND", "local").lower() == "zetaris":
    check("Data: Zetaris web interface", zetaris_web)
    check("Data: Zetaris runs SQL over its REST API", zetaris_sql)
check("Data: Lotline finds every table it needs", lotline_tables)
check("Power: Meterless Runtime (Nightshift) replies", meterless_runtime)
check("Power: NVIDIA Nemotron replies", nvidia_api)

for status, name, detail in results:
    print(f"{status} {name} {detail}")
