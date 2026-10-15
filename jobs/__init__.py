"""Job-posting tools behind the window: evaluate a posting, track applications, tailor a resume.

Resume Studio does not implement these itself. It connects to a tool that already does them well
(today: career-ops) through a small, documented contract (`base.py`), so a better tool can replace
it later without touching the window.
"""
