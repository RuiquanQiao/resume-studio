"""Launch the Resume Studio UI.

    python scripts/studio.py                    # uses ./resume.yaml (created if missing)
    python scripts/studio.py --data E:/Resume/resume.yaml
    python scripts/studio.py --port 9000 --no-browser
"""
from __future__ import annotations

import argparse
import json
import os
import webbrowser

from _bootstrap import ROOT, ensure_deps, resolve_data

ensure_deps()

from core.claude import ClaudeRunner  # noqa: E402
from core.server import App, running_instance  # noqa: E402
from core.watcher import FileWatcher  # noqa: E402
from resume import api  # noqa: E402
from resume.service import Studio  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    studio = Studio(resolve_data(args.data))
    identity = {"app": "resume-studio", "data": str(studio.store.path)}
    state_file = studio.store.state_dir / "server.json"

    # One UI per data file: calling /resume-studio again just reopens the running one.
    url = running_instance(state_file, identity)
    if url:
        print(f"  already running: {url}")
        print(f"  UI running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    if studio.ensure_file():
        print(f"  created a new data file: {studio.store.path}")
    print(f"  Resume Studio  data: {studio.store.path}")

    app = App(static_dir=ROOT / "web")
    app.route("GET", "/api/instance")(lambda req: identity)
    runner = ClaudeRunner(app.bus, cwd=studio.data_dir)
    api.register(app, studio, runner)
    FileWatcher(studio.store.path, studio.store.digest_text,
                lambda digest: app.bus.publish("file", {"digest": digest})).start()

    def ready(url: str) -> None:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")

    def cleanup() -> None:
        try:
            state_file.unlink()
        except OSError:
            pass

    app.serve(port=args.port, open_browser=not args.no_browser, on_ready=ready, on_exit=cleanup)


if __name__ == "__main__":
    main()
