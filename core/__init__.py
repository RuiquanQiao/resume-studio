"""Generic "Skill with a UI" layer.

Nothing in here knows about resumes. It provides:
  - store.YamlStore      round-trip YAML file store (keeps comments/order), atomic writes, local history
  - watcher.FileWatcher  polls the data file and reports external edits (e.g. made by Claude Code)
  - events.EventBus      fan-out of server-sent events to open browser tabs
  - server.App           tiny router on the standard library HTTP server, static files, SSE, free port
  - claude.ClaudeRunner  runs `claude -p` headless and streams its progress as events
"""
