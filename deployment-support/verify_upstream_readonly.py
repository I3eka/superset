"""Read-only API smoke check for this test deployment (credentials stay in memory)."""

import json
import os
from urllib.request import Request, urlopen

base_url = os.environ.get("SUPERSET_VERIFY_URL", "http://superset:8088").rstrip("/")
login = Request(
    base_url + "/api/v1/security/login",
    data=json.dumps(
        {
            "username": "admin",
            "password": os.environ["ADMIN_PASSWORD"],
            "provider": "db",
            "refresh": False,
        }
    ).encode(),
    headers={"Content-Type": "application/json"},
)
with urlopen(login, timeout=30) as response:
    token = json.load(response)["access_token"]


def get(path):
    request = Request(base_url + path, headers={"Authorization": f"Bearer {token}"})
    with urlopen(request, timeout=30) as response:
        assert response.status == 200, path
        return json.load(response)


profiles = get("/api/v1/ai/agent/")["result"]
profiles_by_key = {profile["key"]: profile for profile in profiles}
assert {"default", "analyst", "builder"} <= profiles_by_key.keys(), sorted(profiles_by_key)
assert {"create_virtual_dataset", "generate_chart", "generate_dashboard"} <= set(
    profiles_by_key["builder"]["tools"]
)
models = get("/api/v1/ai/model/")["result"]
assert (os.environ.get("VLLM_MODEL") or os.environ["AI_MODEL"]) in models
counts = {
    resource: get(f"/api/v1/{resource}/?q=(page_size:1)")["count"]
    for resource in ("dashboard", "chart", "dataset", "database")
}
assert counts == {"dashboard": 24, "chart": 223, "dataset": 35, "database": 3}, counts
assert get("/api/v1/dashboard/42")["result"]["id"] == 42
threads = get("/api/v1/ai/thread/")["result"]
for thread in threads:
    get(f"/api/v1/ai/thread/{thread['uuid']}")
print("READONLY_API_OK", json.dumps({"counts": counts, "profiles": sorted(profiles_by_key), "threads_read": len(threads)}))
