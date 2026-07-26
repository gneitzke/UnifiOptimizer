"""SPA serving: index at /, deep links fall back, API routes still win."""

from pathlib import Path

from fastapi.testclient import TestClient

from netadmin.server.main import create_app


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<title>netadmin</title>")
    (dist / "assets" / "app.js").write_text("ok")
    return dist


def test_spa_served_and_api_wins(tmp_path, settings):
    settings.web_dist_path = str(_dist(tmp_path))
    app = create_app(settings)
    with TestClient(app) as c:
        assert "netadmin" in c.get("/").text
        assert "netadmin" in c.get("/issues/42").text  # deep link fallback
        assert c.get("/assets/app.js").text == "ok"
        assert c.get("/api/health").status_code == 200  # API not shadowed


def test_no_dist_no_spa(settings):
    settings.web_dist_path = "/nonexistent/dist"
    app = create_app(settings)
    with TestClient(app) as c:
        assert c.get("/").status_code in (404, 401)
