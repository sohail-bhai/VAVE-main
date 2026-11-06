# VAVE project showcase

Brand tagline: **Built by Builders, For Builders.**

Three dependency-free static pages: overview and interactive explanation,
features/workflow/architecture, and a Windows source-download/setup guide.
The browser walkthrough is explicitly illustrative; it does not operate the
visitor's computer, connect to a VAVE API, or claim to be real execution footage.

## Preview locally

From the repository root, build the sanitized download and serve the website:

```powershell
python build_release.py --output-dir showcase/downloads
python -m http.server 4173 --bind 127.0.0.1 --directory showcase
```

Open http://127.0.0.1:4173. No frontend build, Node install, or assistant process
is required. Static pages remain readable without JavaScript. Fonts are served
locally; system fonts provide a fallback.

## Publish with GitHub Pages

1. Review the finished site, download, and public content before publishing.
2. In repository Settings → Pages, choose **GitHub Actions** as the source.
3. Push authorized changes to `main`, `sohail`, or `codex/vave-showcase`. The showcase workflow verifies
   packaging, generates the source ZIP, and publishes only `showcase/`.
4. Check the deployment and visit https://sohail-bhai.github.io/VAVE-main/.

The publishing workflow also supports manual dispatch. If the repository or
hosting path changes, update canonical/Open Graph URLs, sitemap.xml, robots.txt,
and links before deploying. Source ZIPs and release metadata are generated;
they are excluded from Git to avoid storing stale releases.

## Release contents

`build_release.py` includes allowed application, test, documentation, and asset
trees. It excludes local config, credentials, databases, runtime data, logs,
screenshots, environments, caches, symlinks, and models outside those trees.
It writes fresh generic config, an empty notes file, Windows setup/launch
scripts, and a quickstart. Setup errors remain visible. `release.json` contains
the artifact's SHA-256 and size. The bundled PWA serves paired phone clients;
the separately developed Expo client stays in the original repository tree.
Python and Ollama remain separate prerequisites.

Source links point to `codex/vave-showcase`, matching the initial published
project snapshot. Update those links if future releases use a different branch.

Version is read from pyproject.toml. When changing the version, update static
download fallback links in get-started.html; JavaScript reads release metadata
for the visible version, size, and hash.

## Add real project evidence

The current orbital diagram, approval card, and interactive tasks are conceptual
or illustrative and labelled accordingly. Existing repository mockup images are
not presented as screenshots. To add real evidence:

1. Manually launch VAVE with a working model and use typed input.
2. Record a bounded task showing request, actual actions, an approval where
   applicable, and the visible outcome. Keep secrets and personal data offscreen.
3. Add a compressed video/poster to `assets/`, or use an approved hosted video.
4. Replace or complement the walkthrough with the recording and an accurate
   caption. Avoid claiming a live browser-hosted desktop session.

Design uses a warm graphite/orange palette, original CSS orbital diagram,
SVG icon system, responsive layouts, keyboard-accessible scenario tabs,
clipboard controls, semantic landmarks, and reduced-motion support.
