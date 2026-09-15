"""Working on GitHub: reading issues, searching files, proposing a fix via Pull Request.

Mirrors the GitLab agent architecture. Communicates directly with the GitHub
REST API v3, isolating credentials in the control plane's secret store as
`github_token`. Proposing a fix creates a dedicated branch and opens a Pull
Request without auto-merging.
"""

import base64
import difflib
import json
import logging
import urllib.parse
import urllib.request

from assistant.config import get_setting

logger = logging.getLogger(__name__)

DEFAULT_HOST = "https://api.github.com"
TIMEOUT = 30
MAX_FILE_CHARS = 6000


class GitHubError(Exception):
    """GitHub refused, or could not be reached."""


def _token():
    """The token, taken from the secret store rather than from the caller."""
    try:
        from assistant.control.service import get_control_plane
        plane = get_control_plane()
        if plane.secrets.has("github_token"):
            return plane.secrets.reveal("github_token")
    except Exception:
        pass

    configured = get_setting("github_token", "")
    if configured and not str(configured).startswith("secret://"):
        return configured

    raise GitHubError(
        "No GitHub token. Store one with: "
        "PUT /api/secrets/github_token, or plane.secrets.put('github_token', ...)"
    )


def _host():
    return str(get_setting("github_url", DEFAULT_HOST)).rstrip("/")


def _request(method, path, payload=None, params=None, transport=None):
    """One place that knows how to call GitHub."""
    url = f"{_host()}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    if transport is not None:
        return transport(method, url, payload)

    token = _token()
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "VAVE-Desktop-Assistant")
    if payload is not None:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = parsed.get("message", detail)
        except Exception:
            pass
        raise GitHubError(f"GitHub {error.code}: {detail}")
    except urllib.error.URLError as error:
        raise GitHubError(f"Cannot reach GitHub: {error.reason}")


class GitHubClient:
    """Carries requests to GitHub, or to a test double standing in for it."""

    def __init__(self, transport=None):
        self.transport = transport

    def _call(self, method, path, payload=None, params=None):
        return _request(method, path, payload=payload, params=params, transport=self.transport)

    def list_issues(self, repo, state="open", limit=10):
        params = {"state": state, "per_page": min(100, limit)}
        return self._call("GET", f"repos/{repo}/issues", params=params)

    def get_issue(self, repo, issue_number):
        return self._call("GET", f"repos/{repo}/issues/{issue_number}")

    def issue_comments(self, repo, issue_number):
        return self._call("GET", f"repos/{repo}/issues/{issue_number}/comments")

    def default_branch(self, repo):
        info = self._call("GET", f"repos/{repo}")
        return info.get("default_branch", "main")

    def search_files(self, repo, query):
        params = {"q": f"{query} repo:{repo}"}
        result = self._call("GET", "search/code", params=params)
        return result.get("items", [])

    def read_file(self, repo, path, ref=""):
        params = {"ref": ref} if ref else None
        item = self._call("GET", f"repos/{repo}/contents/{path.lstrip('/')}", params=params)
        content_b64 = item.get("content", "")
        if content_b64:
            try:
                return base64.b64decode(content_b64).decode("utf-8", errors="replace")
            except Exception:
                pass
        return str(item.get("content", ""))

    def create_branch(self, repo, new_branch, base_branch):
        base_ref = self._call("GET", f"repos/{repo}/git/refs/heads/{base_branch}")
        sha = base_ref.get("object", {}).get("sha", "")
        payload = {"ref": f"refs/heads/{new_branch}", "sha": sha}
        return self._call("POST", f"repos/{repo}/git/refs", payload=payload)

    def commit_file(self, repo, branch, path, content, message):
        # Fetch existing file SHA if present
        sha = None
        try:
            existing = self._call("GET", f"repos/{repo}/contents/{path.lstrip('/')}", params={"ref": branch})
            sha = existing.get("sha")
        except Exception:
            pass

        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        return self._call("PUT", f"repos/{repo}/contents/{path.lstrip('/')}", payload=payload)

    def open_pull_request(self, repo, head, base, title, body=""):
        payload = {"title": title, "head": head, "base": base, "body": body}
        return self._call("POST", f"repos/{repo}/pulls", payload=payload)


def _client(transport=None):
    return GitHubClient(transport=transport)


def github_list_issues(repo, state="open", limit=10, _client_override=None):
    """List open issues on a GitHub repo."""
    try:
        issues = (_client_override or _client()).list_issues(repo, state, limit)
    except GitHubError as error:
        return str(error)

    if not issues:
        return f"No {state} issues on {repo}."

    lines = [f"#{item['number']} {item['title']}"
             + (f"  [{', '.join(label.get('name', '') for label in item.get('labels', []))}]"
                if item.get("labels") else "")
             for item in issues]
    return f"{len(issues)} {state} issue(s) on {repo}:\n" + "\n".join(lines)


def github_read_issue(repo, issue_number, _client_override=None):
    """Read one GitHub issue in full, with its comments."""
    client = _client_override or _client()
    try:
        issue = client.get_issue(repo, issue_number)
        comments = client.issue_comments(repo, issue_number)
    except GitHubError as error:
        return str(error)

    labels = [lbl.get("name", "") for lbl in issue.get("labels", [])]
    comment_lines = "\n".join(
        f"- {c.get('user', {}).get('login', 'someone')}: {' '.join(str(c.get('body', '')).split())[:300]}"
        for c in comments
    )

    return (
        f"Issue #{issue.get('number', issue_number)}: {issue.get('title', '')}\n"
        f"State: {issue.get('state')}  Labels: {', '.join(labels) or 'none'}\n\n"
        f"{' '.join(str(issue.get('body') or '').split())[:2000]}\n\n"
        f"Comments:\n{comment_lines or 'none'}"
    )


def github_find_file(repo, query, _client_override=None):
    """Search the GitHub repository for matching filenames or code."""
    try:
        hits = (_client_override or _client()).search_files(repo, query)
    except GitHubError as error:
        return str(error)

    if not hits:
        return f"Nothing in {repo} matches '{query}'."

    lines = [hit.get("path", "") for hit in hits if hit.get("path")]
    return f"Files mentioning '{query}':\n" + "\n".join(dict.fromkeys(lines))


def github_read_file(repo, path, ref="", _client_override=None):
    """Read a file from the repository at a given ref or default branch."""
    client = _client_override or _client()
    try:
        branch = ref or client.default_branch(repo)
        content = client.read_file(repo, path, branch)
    except GitHubError as error:
        return str(error)

    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + "\n... [file trimmed]"
    return f"{path} on {branch}:\n{content}"


def github_propose_fix(repo, issue_number, path, new_content, summary="", _client_override=None):
    """Commit a proposed fix to its own branch and open a Pull Request with a diff preview."""
    client = _client_override or _client()
    branch = f"vave/issue-{issue_number}"

    try:
        target = client.default_branch(repo)
        try:
            client.create_branch(repo, branch, target)
        except GitHubError:
            pass  # Branch exists

        diff_text = ""
        try:
            old_content = client.read_file(repo, path, target)
            diff_lines = list(difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            ))
            if diff_lines:
                diff_text = "\n\n```diff\n" + "".join(diff_lines[:100]) + "\n```"
        except Exception:
            pass

        client.commit_file(
            repo, branch, path, new_content,
            f"Fix #{issue_number}: {summary or 'address the reported issue'}"
        )

        pr = client.open_pull_request(
            repo, branch, target,
            title=f"Fix #{issue_number}: {summary or 'address the reported issue'}",
            body=(f"Closes #{issue_number}\n\n{summary}{diff_text}\n\n"
                  "Prepared by VAVE. Please review before merging.")
        )
    except GitHubError as error:
        return str(error)

    return (
        f"Opened pull request #{pr.get('number', '?')} from {branch} into {target}: {pr.get('html_url', '')}\n"
        "It is not merged. Review and merge it on GitHub once verified."
    )
