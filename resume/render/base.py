from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RenderResult:
    ok: bool
    pages: int = 0
    pdf: Path | None = None
    source: Path | None = None       # the generated .tex / .typ
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    # per bullet: {entry_id: {bullet_index: {"n": lines, "fill": natural width / line width}}}
    lines: dict = field(default_factory=dict)
    cached: bool = False             # same source as the last build: the compiler did not run

    def to_json(self) -> dict:
        return {"ok": self.ok, "pages": self.pages, "seconds": round(self.seconds, 2),
                "pdf": str(self.pdf) if self.pdf else None,
                "source": str(self.source) if self.source else None, "errors": self.errors,
                "lines": self.lines, "cached": self.cached}


class Renderer(ABC):
    """Turns a view (see model.build_view) into a PDF inside `build_dir`."""

    source_suffix = ".txt"

    @abstractmethod
    def templates(self) -> list[dict]:
        """[{id, name, description}]"""

    @abstractmethod
    def render(self, view: dict, build_dir: Path) -> RenderResult:
        ...
