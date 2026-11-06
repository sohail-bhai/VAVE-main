"""Prepare the static showcase and clean source download for hosting."""

import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from build_release import build_release


ROOT = Path(__file__).resolve().parent
DEFAULT_SITE_URL = "https://sohail-bhai.github.io/VAVE-main/"
PAGES = ("index.html", "features.html", "get-started.html")


def hosting_url():
    """Resolve only public hosting metadata, never application credentials."""
    value = os.environ.get("SHOWCASE_SITE_URL")
    if not value:
        host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("VERCEL_URL")
        value = "https://" + host if host else DEFAULT_SITE_URL
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError("SHOWCASE_SITE_URL must be a public HTTP(S) URL without credentials, query, or fragment")
    return value.rstrip("/") + "/"


def configure_urls(site_dir, site_url):
    """Update host-specific metadata while preserving relative and GitHub links."""
    site_dir = Path(site_dir)
    for filename in PAGES:
        page = site_dir / filename
        text = page.read_text(encoding="utf-8")
        canonical = site_url + ("" if filename == "index.html" else filename)
        text = re.sub(r'(<link rel="canonical" href=")[^"]+("\s*>)',
                      lambda match: match[1] + canonical + match[2], text)
        text = re.sub(r'(<meta property="og:url" content=")[^"]+("\s*>)',
                      lambda match: match[1] + canonical + match[2], text)
        text = re.sub(r'(<meta property="og:image" content=")[^"]+("\s*>)',
                      lambda match: match[1] + site_url + "assets/social-card.png?v=builders" + match[2], text)
        page.write_text(text, encoding="utf-8")
    (site_dir / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\nSitemap: " + site_url + "sitemap.xml\n",
        encoding="utf-8",
    )
    urls = "\n".join("  <url><loc>" + escape(site_url + ("" if page == "index.html" else page))
                     + "</loc></url>" for page in PAGES)
    (site_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + urls + "\n</urlset>\n", encoding="utf-8",
    )


def main():
    site_url = hosting_url()
    site_dir = ROOT / "showcase"
    build_release(output_dir=site_dir / "downloads")
    configure_urls(site_dir, site_url)
    print("Prepared static showcase and clean source download.")


if __name__ == "__main__":
    main()
