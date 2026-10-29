import pytest

from test_ui import browser_ctx, page_all  # fixtures for the real, headless UI

pytestmark = pytest.mark.e2e


def test_archive_restore_ui(page_all, server):
    page = page_all
    cid = server.req("POST", "/api/chats", {"agent": "codex"})[1]["id"]
    server.inst.chats.store.update_meta(cid, agent="codex", session_id="t-example")
    page.reload()
    page.wait_for_selector("#chat-archive:not([disabled])")
    page.click("#chat-archive")
    page.wait_for_function("() => document.querySelector('#chat-input').disabled", timeout=5000)
    assert page.locator("#chat-input").is_disabled()
    assert server.req("GET", f"/api/chats/{cid}")[1]["chat"]["archived"]
    page.click("#chat-archive")
    page.wait_for_function("() => !document.querySelector('#chat-input').disabled", timeout=5000)
    assert not server.req("GET", f"/api/chats/{cid}")[1]["chat"]["archived"]
