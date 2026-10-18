"""Company logos (docs/JOB_HUNT.md, 2026-10-18): a company with a site gets its logo from that site by
itself, with no Claude: the icon the site's own pages declare, else /favicon.ico. Sites run on a hiring
system (Moka, Feishu, Greenhouse…) are skipped: their icon is the vendor's, not the company's."""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from core.i18n import t

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
# hosts whose icon is the hiring system's own
VENDORS = ("mokahr.com", "feishu.cn", "larksuite.com", "zhiye.com", "hotjob.cn", "beisen.com", "greenhouse.io",
           "lever.co", "myworkdayjobs.com", "workday.com", "ashbyhq.com", "smartrecruiters.com", "linkedin.com",
           "zhipin.com", "liepin.com", "51job.com", "zhaopin.com", "nowcoder.com", "dajie.com", "wondercv.com")


def fetch_logo(url: str) -> tuple[bytes, str]:
    """Download a logo (http(s) only, a picture, under 2 MB)."""
    if not url.startswith(("https://", "http://")):
        raise ValueError(t("The logo's address must start with https://"))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read(2_000_001)
            ctype = r.headers.get("Content-Type") or ""
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ValueError(t("Couldn't download the logo: {error}", error=str(getattr(e, "reason", e))))
    if ctype.startswith(("application/octet-stream", "binary/", "text/plain")) or not ctype:   # some CDNs don't say
        head = data[:16]
        ctype = ("image/png" if head.startswith(b"\x89PNG") else "image/jpeg" if head.startswith(b"\xff\xd8")
                 else "image/gif" if head.startswith(b"GIF8") else "image/webp" if head[8:12] == b"WEBP"
                 else "image/x-icon" if head.startswith(b"\x00\x00\x01\x00")
                 else "image/svg+xml" if b"<svg" in data[:400] else ctype)
    return data, ctype


class _Icons(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[int, str]] = []   # (rank, href): lower is better

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel, href = a.get("rel", "").lower().split(), a.get("href", "").strip()
        if not href or href.startswith("data:"):
            return
        if "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
            rank = 0
        elif "icon" in rel or "shortcut" in rel:
            sizes = [int(n) for n in re.findall(r"(\d+)x\d+", a.get("sizes", ""))]
            big = max(sizes or [0])
            rank = 1 if href.lower().split("?")[0].endswith(".svg") or big >= 64 else 2 if big >= 32 or not sizes else 3
        elif "mask-icon" in rel:
            return   # a one-colour silhouette
        else:
            return
        self.found.append((rank, href))


def is_vendor(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return any(host == v or host.endswith("." + v) for v in VENDORS)


def candidates(page_url: str, html: str) -> list[str]:
    """The icons a page declares, best first, then the site's /favicon.ico."""
    p = _Icons()
    try:
        p.feed(html)
    except Exception:   # a broken page: fall back to /favicon.ico
        pass
    out = [urllib.parse.urljoin(page_url, href) for _, href in sorted(p.found, key=lambda x: x[0])]
    parts = urllib.parse.urlsplit(page_url)
    out.append(f"{parts.scheme}://{parts.netloc}/favicon.ico")
    return [u for u in dict.fromkeys(out) if u.startswith(("https://", "http://"))]


def _page(url: str) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.geturl(), r.read(600_000).decode("utf-8", "ignore")


def from_sites(sites: list[str], *, page=_page, download=fetch_logo, types: dict | None = None) -> tuple[bytes, str, str] | None:
    """The first logo found on a company's own sites: (data, content type, where it came from), or None."""
    for site in sites:
        if not site or is_vendor(site):
            continue
        try:
            final, html = page(site)
        except (urllib.error.URLError, OSError, ValueError):
            final, html = site, ""
        if is_vendor(final):
            continue
        for u in candidates(final, html):
            try:
                data, ctype = download(u)
            except ValueError:
                continue
            ctype = ctype.split(";")[0].strip().lower()
            if data and (types is None or ctype in types):
                return data, ctype, u
    return None
