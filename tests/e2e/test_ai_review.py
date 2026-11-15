from test_ui import browser_ctx, page_all  # shared real application fixtures


def test_ai_review_shows_only_changed_language_and_interview_answer(page_all):
    page_all.evaluate('''() => {
      const before = {id: "review-test", bullets: [{en: "Built a useful app", zh: "开发了产品"}, {en: "Unchanged", zh: "保持不变"}], interview: [{q: "Why?", a: "Old answer"}]};
      const after = structuredClone(before);
      after.bullets[0].zh = "开发了优秀产品";
      after.interview[0].a = "New answer";
      ui.ai[before.id] = {snapshot: before, result: after, action: "polish", done: true};
      const host = document.createElement('div'); host.id = 'review-test'; document.body.append(host); host.append(aiDiff(before.id));
    }''')
    review = page_all.locator('#review-test')
    assert review.locator('.ai-change').count() == 2
    assert 'Unchanged' not in review.inner_text()
    assert 'Built a useful app' not in review.inner_text()
    assert 'Old answer' in review.inner_text()
    assert 'New answer' in review.inner_text()
    assert review.locator('mark').count() > 0
    # git-style: − the old line, + the new one (2026-11-15: "Before / After" words were the odd one out)
    assert review.locator('.ai-change .dl-del').count() == 2 and review.locator('.ai-change .dl-add').count() == 2
    assert review.locator('.ai-change-side').count() == 0


def test_the_reply_lists_every_changed_entry_to_open_keep_or_undo(page_all):
    """2026-11-15: a batch changed several entries and each had to be found and approved on its own card. Under the
    reply now: each changed entry with its +/− lines, a click opens its diff, Keep all keeps every one."""
    got = page_all.evaluate('''() => {
      const [a, b] = entries();
      const was = { messages: ui.chat.messages, running: ui.chat.running };
      // a long chat: the log is a column of flex items, and the list was squeezed with its rows spilling out, unclickable
      ui.chat.messages = Array.from({ length: 30 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", text: "Message " + i, blocks: [{ type: "text", text: "Reply " + i }] }));
      ui.chat.running = false;
      for (const e of [a, b]) {
        const after = structuredClone(e); after.subtitle = { en: "Changed by the AI" };
        ui.ai[e.id] = { chat: ui.chat.id, action: "batch_fit", snapshot: structuredClone(e), result: after, done: true };
      }
      renderChat();
      const box = document.querySelector("#chat-log .rv-box");
      const out = { rows: box ? box.querySelectorAll(".rv-row").length : 0, head: box ? box.querySelector(".rv-head").innerText : "",
        stat: box ? box.querySelector(".rv-stat").innerText : "", ids: [a.id, b.id],
        inside: box ? [...box.querySelectorAll(".rv-row")].every((r) => r.getBoundingClientRect().bottom <= box.getBoundingClientRect().bottom + 0.5) : false };
      window.__was = was;
      return out;
    }''')
    assert got["rows"] == 2 and "2" in got["head"] and "+" in got["stat"] and "−" in got["stat"]
    assert got["inside"], "the list's rows spill out of it in a long chat"
    page_all.click("#chat-log .rv-head .btn.primary")                       # Keep all
    page_all.wait_for_selector("#chat-log .rv-box", state="detached")
    assert page_all.evaluate("(ids) => ids.filter((id) => ui.ai[id]).length", got["ids"]) == 0
    page_all.evaluate("() => { Object.assign(ui.chat, window.__was); renderChat(); }")


def test_review_move_and_undo_guard(page_all):
    result = page_all.evaluate('''async () => {
      const entry = entries()[0];
      const before = structuredClone(entry);
      const after = structuredClone(entry);
      before.bullets = [{en: "First", zh: "第一条"}, {en: "Second", zh: "第二条"}, {en: "Third", zh: "第三条"}];
      after.bullets = [before.bullets[1], before.bullets[2], before.bullets[0]];
      const changes = aiChanges(before, after);
      const current = structuredClone(entry);
      const fakeResult = structuredClone(current);
      fakeResult.link = "AI result";
      const fakeBefore = structuredClone(current);
      fakeBefore.link = "Before";
      ui.ai[entry.id] = {snapshot: fakeBefore, result: fakeResult, done: true};
      let called = false, message = "";
      const originalApi = api, originalToast = toast;
      api = async () => { called = true; }; toast = (s) => { message = s; };
      try { await undoAi(entry.id); } finally { api = originalApi; toast = originalToast; delete ui.ai[entry.id]; }
      return {changes, called, message};
    }''')
    moves = [c for c in result['changes'] if c.get('move')]
    assert len(moves) == 1
    assert (moves[0]['from'], moves[0]['to']) == (1, 3)
    assert not result['called']
    assert result['message']


def test_review_long_text_layout(page_all):
    page_all.set_viewport_size({'width': 1100, 'height': 680})
    result = page_all.evaluate('''() => {
      const before = {id: "layout-review", bullets: [{zh: "这是很长的内容，".repeat(25), en: "Long content " .repeat(20)}]};
      const after = structuredClone(before); after.bullets[0].zh += "新增内容";
      ui.ai[before.id] = {snapshot: before, result: after, done: true};
      const host = document.createElement('div'); host.style.cssText = 'position:fixed;left:0;top:0;width:310px;height:650px;overflow:auto;z-index:9999;background:white';
      host.id = 'layout-review'; document.body.append(host); host.append(aiDiff(before.id));
      const card = host.querySelector('.ai-review');
      return {scroll: card.scrollWidth, width: card.clientWidth, height: card.scrollHeight};
    }''')
    assert result['scroll'] <= result['width'] + 1
    assert result['height'] > 100
    page_all.locator('#layout-review').screenshot(path='.cache/ai-review-check/long-review.png')
