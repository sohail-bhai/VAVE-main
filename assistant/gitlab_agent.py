"""Working on GitLab: reading issues, proposing a fix, opening a merge request.

The browser is how VAVE reads a page a person would read. GitLab is
different: it has a proper API, and using it means a fix lands as a real
commit on a real branch rather than as clicks that might have gone wrong. So
this talks to the API, and the browser is only for showing you the result.

The token is never held here. It is stored in the control plane's secret
store as `gitlab_token`, resolved at the moment a request is made, and the
merge itself is a critical capability, so it stops and asks you first.
"""

import json
import logging
import urllib.parse
import urllib.request

from assistant.config import get_setting

logger = logging.getLogger(__name__)

DEFAULT_HOST = "https://gitlab.com"
TIMEOUT = 30

# Enough of a file to reason about without flooding a small model.
MAX_FILE_CHARS = 6000


class GitLabError(Exception):
    """GitLab refused, or could not be reached."""


def _token():
    """The token, taken from the secret store rather than from the caller."""
    from assistant.control.service import get_control_plane

    plane = get_control_plane()
    if plane.secrets.has("gitlab_token"):
        return plane.secrets.reveal("gitlab_token")

    configured = get_setting("gitlab_token", "")
    if configured and not str(configured).startswith("secret://"):
        return configured

    raise GitLabError(
        "No GitLab token. Store one with: "
        "PUT /api/secrets/gitlab_token, or plane.secrets.put('gitlab_token', ...)")


def _host():
    return str(get_setting("gitlab_url", DEFAULT_HOST)).rstrip("/")


def _request(method, path, payload=None, params=None, transport=None):
    """One place that knows how to call GitLab."""
    url = f"{_host()}/api/v4/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    if transport is not None:
        return transport(method, url, payload)

    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("PRIVATE-TOKEN", _token())
    request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            text = response.read().decode("utf-8")
    except Exception as error:
        raise GitLabError(f"GitLab said no: {error}") from error

    return json.loads(text) if text else {}


def _project_path(project):
    """GitLab wants the project path URL-encoded: group/repo -> group%2Frepo."""
    return urllib.parse.quote(str(project).strip("/"), safe="")


class GitLabClient:
    """The operations a fix actually needs. Transport is injectable for tests."""

    def __init__(self, transport=None):
        self.transport = transport

    def call(self, method, path, payload=None, params=None):
        return _request(method, path, payload=payload, params=params,
                        transport=self.transport)

    # -- reading ------------------------------------------------------------

    def list_issues(self, project, state="opened", limit=10):
        return self.call("GET", f"projects/{_project_path(project)}/issues",
                         params={"state": state, "per_page": int(limit)})

    def get_issue(self, project, iid):
        return self.call("GET", f"projects/{_project_path(project)}/issues/{iid}")

    def issue_notes(self, project, iid):
        return self.call("GET",
                         f"projects/{_project_path(project)}/issues/{iid}/notes",
                         params={"per_page": 20})

    def search_files(self, project, query):
        return self.call("GET", f"projects/{_project_path(project)}/search",
                         params={"scope": "blobs", "search": query, "per_page": 10})

    def read_file(self, project, path, ref="main"):
        encoded = urllib.parse.quote(path, safe="")
        result = self.call(
            "GET", f"projects/{_project_path(project)}/repository/files/{encoded}/raw",
            params={"ref": ref})
        return result if isinstance(result, str) else json.dumps(result)

    def default_branch(self, project):
        info = self.call("GET", f"projects/{_project_path(project)}")
        return info.get("default_branch", "main")

    # -- writing ------------------------------------------------------------

    def create_branch(self, project, branch, ref):
        return self.call("POST", f"projects/{_project_path(project)}/repository/branches",
                         payload={"branch": branch, "ref": ref})

    def commit(self, project, branch, message, changes):
        """One commit, several files. `changes` is {path: new content}."""
        actions = [{"action": "update", "file_path": path, "content": content}
                   for path, content in changes.items()]
        return self.call("POST", f"projects/{_project_path(project)}/repository/commits",
                         payload={"branch": branch, "commit_message": message,
                                  "actions": actions})

    def open_merge_request(self, project, source, target, title, description=""):
        return self.call("POST", f"projects/{_project_path(project)}/merge_requests",
                         payload={"source_branch": source, "target_branch": target,
                                  "title": title, "description": description,
                                  "remove_source_branch": True})

    def merge(self, project, merge_request_iid):
        return self.call(
            "PUT",
            f"projects/{_project_path(project)}/merge_requests/{merge_request_iid}/merge")


# -- the tools the model calls ---------------------------------------------
# Each returns plain text, because that text goes back into the conversation.

def _client(transport=None):
    return GitLabClient(transport=transport)


def gitlab_list_issues(project, state="opened", limit=10, _client_override=None):
    """List open issues on a GitLab project, so VAVE can pick one."""
    try:
        issues = (_client_override or _client()).list_issues(project, state, limit)
    except GitLabError as error:
        return str(error)

    if not issues:
        return f"No {state} issues on {project}."

    lines = [f"#{item['iid']} {item['title']}"
             + (f"  [{', '.join(item.get('labels', []))}]" if item.get("labels") else "")
             for item in issues]
    return f"{len(issues)} {state} issue(s) on {project}:\n" + "\n".join(lines)


def gitlab_read_issue(project, issue_iid, _client_override=None):
    """Read one issue in full, with its comments, to understand what is wanted."""
    client = _client_override or _client()
    try:
        issue = client.get_issue(project, issue_iid)
        notes = client.issue_notes(project, issue_iid)
    except GitLabError as error:
        return str(error)

    comments = "\n".join(f"- {note.get('author', {}).get('name', 'someone')}: "
                         f"{' '.join(str(note.get('body', '')).split())[:300]}"
                         for note in notes if not note.get("system"))

    return (f"Issue #{issue['iid']}: {issue['title']}\n"
            f"State: {issue.get('state')}  Labels: {', '.join(issue.get('labels', [])) or 'none'}\n\n"
            f"{' '.join(str(issue.get('description') or '').split())[:2000]}\n\n"
            f"Comments:\n{comments or 'none'}")


def gitlab_find_file(project, query, _client_override=None):
    """Search the repository for the file an issue is talking about."""
    try:
        hits = (_client_override or _client()).search_files(project, query)
    except GitLabError as error:
        return str(error)

    if not hits:
        return f"Nothing in {project} matches '{query}'."

    lines = [f"{hit.get('path')} (line {hit.get('startline', '?')})" for hit in hits]
    return f"Files mentioning '{query}':\n" + "\n".join(dict.fromkeys(lines))


def gitlab_read_file(project, path, ref="", _client_override=None):
    """Read a file from the repository, so a fix is written against real code."""
    client = _client_override or _client()
    try:
        branch = ref or client.default_branch(project)
        content = client.read_file(project, path, branch)
    except GitLabError as error:
        return str(error)

    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + "\n... [file trimmed]"
    return f"{path} on {branch}:\n{content}"


def gitlab_propose_fix(project, issue_iid, path, new_content, summary="",
                       _client_override=None):
    """Put a fix on its own branch and open a merge request for it.

    Nothing is merged here. This is the point where a person can read the
    change - which is why proposing and merging are two different steps.
    """
    client = _client_override or _client()
    branch = f"vave/issue-{issue_iid}"

    try:
        target = client.default_branch(project)
        try:
            client.create_branch(project, branch, target)
        except GitLabError:
            pass        # the branch already exists; commit onto it

        diff_text = ""
        try:
            import difflib
            old_content = client.read_file(project, path, target)
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

        client.commit(project, branch,
                      f"Fix #{issue_iid}: {summary or 'address the reported issue'}",
                      {path: new_content})

        merge_request = client.open_merge_request(
            project, branch, target,
            title=f"Fix #{issue_iid}: {summary or 'address the reported issue'}",
            description=(f"Closes #{issue_iid}\n\n{summary}{diff_text}\n\n"
                         "Prepared by VAVE. Please read the change before merging."))
    except GitLabError as error:
        return str(error)

    return (f"Opened merge request !{merge_request.get('iid')} from {branch} "
            f"into {target}: {merge_request.get('web_url', '')}\n"
            f"It is not merged. Ask me to merge it once you have read it.")


def gitlab_merge(project, merge_request_iid, _client_override=None):
    """Merge a merge request. This is the consequential one."""
    try:
        result = (_client_override or _client()).merge(project, merge_request_iid)
    except GitLabError as error:
        return str(error)

    return (f"Merged !{merge_request_iid} into {result.get('target_branch', 'the default branch')}. "
            f"{result.get('web_url', '')}")
