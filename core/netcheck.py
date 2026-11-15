"""Can this computer reach an agent's service at all?

An agent that can't reach its service often says nothing while it waits (Codex spent 87 s on "workspace
routing discovery" with no event, 2026-11-15, because a proxy's DNS answered chatgpt.com with a poisoned
address). When a reply has been quiet for a while, the chat asks this module, so the window can say
"can't reach chatgpt.com" instead of only "Working…".

The probe is one HTTPS request per host, through the same system proxy settings the agents use. Any HTTP
answer, even 403, means the host is reachable; only no answer, a refused connection or a certificate for
the wrong site (what a poisoned DNS answer leads to) counts as unreachable. Nothing of the user's is sent.
"""
from __future__ import annotations

import threading
import urllib.error
import urllib.request

# The hosts each agent's replies come from. An agent not listed is never probed.
SERVICE_HOSTS: dict[str, tuple[str, ...]] = {
    "claude": ("api.anthropic.com",),
    "codex": ("chatgpt.com", "api.openai.com"),   # signed in with ChatGPT / with an API key
}


def reachable(host: str, timeout: float = 8.0) -> bool:
    req = urllib.request.Request(f"https://{host}/", method="HEAD", headers={"User-Agent": "resume-studio-netcheck"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True      # it answered
    except (OSError, ValueError):
        return False     # no answer, refused, wrong certificate (URLError and timeouts are OSErrors)


def unreachable(hosts: tuple[str, ...] | list[str], timeout: float = 8.0) -> list[str]:
    """The hosts that don't answer, probed side by side (so the wait is one timeout, not one per host)."""
    results: dict[str, bool] = {}
    threads = [threading.Thread(target=lambda h=h: results.__setitem__(h, reachable(h, timeout)), daemon=True)
               for h in hosts]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout + 2)
    return [h for h in hosts if not results.get(h, False)]
