"""main.py — /api/config ve /api/formats uç testleri (motor mock'lanır)."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import FormatInfo, FormatsResponse
from app.ytdlp_engine import EngineError

client = TestClient(app)

FAKE_RESPONSE = FormatsResponse(
    title="Mock Video",
    duration=10.0,
    thumbnail=None,
    uploader="X",
    formats=[FormatInfo(format_id="18", ext="mp4", resolution="640x360",
                        height=360, kind="combined")],
    presets=["best", "720p"],
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
    assert resp.json()["title"] == "Mock Video"


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
