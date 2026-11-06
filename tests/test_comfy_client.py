import httpx
import pytest

from app.services.comfy_client import (
    ComfyClient,
    ComfyConnectionError,
    ComfyExecutionError,
    ComfyPromptError,
    ComfyTimeoutError,
)
from tests.fake_comfy import FakeComfy

PROMPT = {"9": {"class_type": "SaveImage", "inputs": {}, "_meta": {"title": "OUTPUT"}}}


async def test_run_success_with_progress_and_download():
    fake = FakeComfy()
    events = []
    async with fake.client() as c:
        res = await c.run(PROMPT, timeout_s=5, on_progress=events.append)
        assert res.prompt_id
        assert [f.filename for f in res.files] == [f"{res.prompt_id}.png"]
        assert await c.download(res.files[0]) == fake.image
    progress = [e for e in events if e.type == "progress"]
    assert [(e.value, e.max) for e in progress] == [(1, 2), (2, 2)]  # 他人的 prompt 事件被過濾
    assert fake.prompts[0]["client_id"] == c.client_id
    assert all(s.closed for s in fake.sockets)


async def test_async_progress_callback():
    fake = FakeComfy()
    seen = []

    async def cb(ev):
        seen.append(ev.type)

    async with fake.client() as c:
        await c.run(PROMPT, timeout_s=5, on_progress=cb)
    assert "progress" in seen


async def test_execution_error_raises_with_node_info():
    fake = FakeComfy(mode="error")
    async with fake.client() as c:
        with pytest.raises(ComfyExecutionError) as ei:
            await c.run(PROMPT, timeout_s=5)
    assert ei.value.node_id == "5" and ei.value.node_type == "KSampler"
    assert "CUDA out of memory" in str(ei.value)


async def test_prompt_rejected_reports_node_errors():
    fake = FakeComfy(mode="reject")
    async with fake.client() as c:
        with pytest.raises(ComfyPromptError) as ei:
            await c.run(PROMPT, timeout_s=5)
    assert "5" in ei.value.node_errors
    assert "sampler_name" in str(ei.value)


async def test_timeout_interrupts():
    fake = FakeComfy(mode="hang")
    async with fake.client() as c:
        with pytest.raises(ComfyTimeoutError):
            await c.run(PROMPT, timeout_s=0.2)
    assert fake.interrupted


async def test_falls_back_to_polling_when_ws_unavailable():
    fake = FakeComfy(ws_fails=True)
    async with fake.client() as c:
        res = await c.run(PROMPT, timeout_s=5, poll_interval_s=0.01)
    assert res.files


async def test_ws_drop_mid_run_falls_back_to_polling():
    fake = FakeComfy(mode="ws_drop")
    async with fake.client() as c:
        res = await c.run(PROMPT, timeout_s=5, poll_interval_s=0.01)
    assert res.files


async def test_upload_image_and_ref(tmp_path):
    fake = FakeComfy()
    p = tmp_path / "ref.png"
    p.write_bytes(b"png")
    async with fake.client() as c:
        res = await c.upload_image(p, subfolder="coloring_books")
        assert ComfyClient.uploaded_ref(res) == "coloring_books/ref.png"
        res2 = await c.upload_image(b"x", "a.png")
        assert ComfyClient.uploaded_ref(res2) == "a.png"
        with pytest.raises(ValueError):
            await c.upload_image(b"x")


async def test_list_models_unknown_folder_returns_none():
    fake = FakeComfy(models={"checkpoints": ["a.safetensors"]})
    async with fake.client() as c:
        assert await c.list_models("checkpoints") == ["a.safetensors"]
        assert await c.list_models("ipadapter") is None


async def test_connection_error_is_wrapped():
    def boom(request):
        raise httpx.ConnectError("refused")

    async with ComfyClient("http://x", transport=httpx.MockTransport(boom)) as c:
        with pytest.raises(ComfyConnectionError, match="無法連線"):
            await c.system_stats()


async def test_non_comfy_server_detected():
    def html(request):
        return httpx.Response(200, text="<html>Dataset Viewer</html>")

    async with ComfyClient("http://x", transport=httpx.MockTransport(html)) as c:
        with pytest.raises(ComfyConnectionError, match="不是 JSON"):
            await c.system_stats()


def test_ws_url():
    assert ComfyClient("https://h:1/", client_id="abc").ws_url == "wss://h:1/ws?clientId=abc"
