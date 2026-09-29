"""Renderer registry. Add a Typst (or HTML) renderer by implementing base.Renderer."""
from __future__ import annotations

from .base import Renderer, RenderResult
from .latex import LatexRenderer

_RENDERERS: dict[str, Renderer] = {"latex": LatexRenderer()}


def get_renderer(engine: str = "latex") -> Renderer:
    if engine not in _RENDERERS:
        raise KeyError(f"unknown render engine: {engine}")
    return _RENDERERS[engine]


def all_templates() -> list[dict]:
    out = []
    for engine, r in _RENDERERS.items():
        for t in r.templates():
            out.append({**t, "engine": engine})
    return out


__all__ = ["Renderer", "RenderResult", "get_renderer", "all_templates"]
