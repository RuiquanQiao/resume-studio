"""Which site a host belongs to, shared by the sign-in checks (autofill) and the careers sites (jobhunt)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit


def base_domain(host: str) -> str:
    """The site a host belongs to: leapmotor.zhiye.com -> zhiye.com, a.b.com.cn -> b.com.cn."""
    host = (host or "").lower().strip(".")
    if re.fullmatch(r"[\d.]+|\[.*\]", host):
        return host
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in ("com", "net", "org", "gov", "edu", "ac", "co") and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def url_domain(url: str) -> str:
    """An address's site: https://cn.wifi.com/jobList.html -> wifi.com."""
    return base_domain(urlsplit(url if "//" in url else "https://" + url).hostname or "")
