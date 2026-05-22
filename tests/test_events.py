"""events.py — SSE ilerleme akışı testleri."""
from app import config
from app.events import event_stream


async def test_event_stream_yields_sse_frame():
    gen = event_stream(lambda: [{"job_id": "j1", "status": "downloading"}])
    try:
        frame = await gen.__anext__()
    finally:
        await gen.aclose()
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    assert "j1" in frame


async def test_event_stream_reflects_current_snapshot(monkeypatch):
    monkeypatch.setattr(config, "EVENT_INTERVAL", 0.0)  # testi hızlandır
    state = [{"job_id": "a"}]
    gen = event_stream(lambda: state)
    try:
        first = await gen.__anext__()
        assert "a" in first
        state[0] = {"job_id": "b"}
        second = await gen.__anext__()
    finally:
        await gen.aclose()
    assert "b" in second
