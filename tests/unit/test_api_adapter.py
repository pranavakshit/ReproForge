from fastapi.testclient import TestClient

from backend.app.api.app import create_app


def test_optional_fastapi_routes():
    app = create_app()
    with TestClient(app) as client:
        h = client.get("/health")
        assert h.status_code == 200
        assert h.json()["status"] == "ok"

        ov = client.get("/api/overview")
        assert ov.status_code == 200
        assert "total_builds" in ov.json()

        repos = client.get("/api/repositories")
        assert repos.status_code == 200
        assert "repositories" in repos.json()

        blds = client.get("/api/builds")
        assert blds.status_code == 200
        assert "builds" in blds.json()

        caus = client.get("/api/causality/latest")
        assert caus.status_code == 200
        assert "nodes" in caus.json()
