"""main.py — /api/config ve /api/formats uç testleri (motor mock'lanır)."""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import FormatInfo, FormatsResponse, ScanResponse
from app.ytdlp_engine import EngineError

client = TestClient(app)

FAKE_RESPONSE = ScanResponse(
    type="video",
    video=FormatsResponse(
        title="Mock Video",
        duration=10.0,
        thumbnail=None,
        uploader="X",
        formats=[FormatInfo(format_id="18", ext="mp4", resolution="640x360",
                            height=360, kind="combined")],
        presets=["best", "720p"],
    ),
)


def test_get_config():
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "default_download_dir" in data
    assert "chrome" in data["browsers"]
    assert "best" in data["presets"]


def test_post_formats_success():
    with patch("app.main.ytdlp_engine.list_formats", return_value=FAKE_RESPONSE):
        resp = client.post("/api/formats", json={"url": "https://x/v"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "video"
    assert body["video"]["title"] == "Mock Video"


def test_post_formats_blank_url_rejected():
    resp = client.post("/api/formats", json={"url": "  "})
    assert resp.status_code == 422


def test_post_formats_engine_error_is_400():
    with patch("app.main.ytdlp_engine.list_formats", side_effect=EngineError("video yok")):
        resp = client.post("/api/formats", json={"url": "https://x/v"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "video yok"


def test_post_formats_unexpected_error_is_500_without_leak():
    with patch("app.main.ytdlp_engine.list_formats", side_effect=RuntimeError("ic ayrinti")):
        resp = client.post("/api/formats", json={"url": "https://x/v"})
    assert resp.status_code == 500
    assert "ic ayrinti" not in resp.json()["detail"]


def test_post_job_returns_job_id(tmp_path):
    resp = client.post("/api/jobs", json={
        "url": "https://example.com/v", "selection": "best",
        "download_dir": str(tmp_path),
    })
    assert resp.status_code == 200
    assert resp.json()["job_id"]


def test_post_job_invalid_url_rejected(tmp_path):
    resp = client.post("/api/jobs", json={
        "url": "not-a-url", "selection": "best", "download_dir": str(tmp_path),
    })
    assert resp.status_code == 422


def test_get_jobs_returns_list():
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_delete_unknown_job_is_404():
    resp = client.delete("/api/jobs/bilinmeyen-kimlik")
    assert resp.status_code == 404


def test_delete_queued_job_cancels_it(tmp_path):
    created = client.post("/api/jobs", json={
        "url": "https://example.com/v", "selection": "best",
        "download_dir": str(tmp_path),
    }).json()["job_id"]
    resp = client.delete(f"/api/jobs/{created}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_lifespan_starts_and_stops_worker_cleanly():
    # `with` bloğu lifespan başlangıç/bitişini (worker start/stop) tetikler.
    with TestClient(app) as managed:
        assert managed.get("/api/config").status_code == 200


def test_cors_allows_chrome_extension_origin():
    origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    resp = client.get("/api/config", headers={"Origin": origin})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == origin


def test_cors_rejects_random_website_origin():
    # Eklenti olmayan kaynaklara CORS başlığı verilmez → tarayıcı okumayı engeller.
    resp = client.get("/api/config", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_cors_preflight_for_extension_post():
    origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    resp = client.options("/api/jobs", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
    })
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == origin


def test_index_serves_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Pluck" in resp.text


def test_static_assets_served():
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_pick_folder_get_returns_state():
    resp = client.get("/api/pick-folder")
    assert resp.status_code == 200
    body = resp.json()
    assert "pending" in body
    assert "path" in body


def test_pick_folder_post_starts_picker():
    # Gerçek tkinter penceresi açılmasın diye picker görevi mock'lanır.
    from app import main as main_module
    main_module._picker_state["pending"] = False
    with patch("app.main._run_folder_picker", new=AsyncMock()):
        resp = client.post("/api/pick-folder")
    assert resp.status_code == 200
    assert resp.json()["pending"] is True
    main_module._picker_state["pending"] = False


def test_events_endpoint_is_sse():
    # Sonsuz akışı tüketmemek için event_stream sınırlı bir üreteçle değiştirilir.
    async def _finite(_get_snapshot):
        yield "data: []\n\n"

    with patch("app.main.event_stream", _finite):
        with TestClient(app) as managed:
            resp = managed.get("/api/events")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.text.startswith("data: ")
