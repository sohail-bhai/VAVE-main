"""Unit tests for Phase 9 Developer Automation (Wave 5: GitHub Agent & Diff Previews)."""

import unittest

from assistant.gitlab_agent import GitLabClient, gitlab_propose_fix
from assistant.github_agent import (
    GitHubClient,
    GitHubError,
    github_find_file,
    github_list_issues,
    github_propose_fix,
    github_read_file,
    github_read_issue,
)


class FakeGitHubTransport:
    """Mock GitHub API transport recording calls and returning canned responses."""

    def __init__(self, responses=None, fail=None):
        self.calls = []
        self.fail = fail or set()
        self.responses = responses or {}

    def __call__(self, method, url, payload=None):
        self.calls.append({"method": method, "url": url, "payload": payload})

        for marker in self.fail:
            if marker in url:
                raise GitHubError(f"GitHub rejected: 403 on {marker}")

        path = url.split("?")[0]
        for marker in sorted(self.responses, key=len, reverse=True):
            if path.endswith(marker):
                return self.responses[marker]
        for marker in sorted(self.responses, key=len, reverse=True):
            if marker in url:
                return self.responses[marker]
        return {}


class DeveloperGitTests(unittest.TestCase):
    def test_github_list_issues_formats_cleanly(self):
        transport = FakeGitHubTransport(responses={
            "/issues": [
                {"number": 42, "title": "Fix memory leak in parser", "labels": [{"name": "bug"}]},
                {"number": 43, "title": "Add documentation for API", "labels": []},
            ]
        })
        client = GitHubClient(transport=transport)
        result = github_list_issues("org/app", _client_override=client)

        self.assertIn("#42 Fix memory leak in parser", result)
        self.assertIn("[bug]", result)
        self.assertIn("#43 Add documentation for API", result)

    def test_github_read_issue_includes_body_and_comments(self):
        transport = FakeGitHubTransport(responses={
            "/issues/42": {"number": 42, "title": "Crash on empty input", "state": "open", "labels": [], "body": "Stack trace here"},
            "/issues/42/comments": [{"user": {"login": "octocat"}, "body": "Confirmed reproducing."}],
        })
        client = GitHubClient(transport=transport)
        result = github_read_issue("org/app", 42, _client_override=client)

        self.assertIn("Issue #42: Crash on empty input", result)
        self.assertIn("Stack trace here", result)
        self.assertIn("octocat: Confirmed reproducing.", result)

    def test_github_propose_fix_creates_branch_and_opens_pr_with_diff(self):
        transport = FakeGitHubTransport(responses={
            "repos/org/app": {"default_branch": "main"},
            "refs/heads/main": {"object": {"sha": "base123"}},
            "git/refs": {"ref": "refs/heads/vave/issue-42"},
            "contents/app.py": {"content": "cHJpbnQoJ2hlbGxvJyk="},  # base64 "print('hello')"
            "pulls": {"number": 101, "html_url": "https://github.com/org/app/pull/101"},
        })
        client = GitHubClient(transport=transport)
        result = github_propose_fix(
            "org/app",
            42,
            "app.py",
            "print('hello world')",
            summary="support world printing",
            _client_override=client,
        )

        self.assertIn("Opened pull request #101", result)
        self.assertIn("https://github.com/org/app/pull/101", result)

        # Check PR payload body included diff block
        pr_call = next(c for c in transport.calls if "pulls" in c["url"])
        body = pr_call["payload"]["body"]
        self.assertIn("```diff", body)
        self.assertIn("-print('hello')", body)
        self.assertIn("+print('hello world')", body)

    def test_gitlab_propose_fix_includes_diff_preview(self):
        class FakeGitLabTransport:
            def __init__(self):
                self.calls = []

            def __call__(self, method, url, payload=None):
                self.calls.append({"method": method, "url": url, "payload": payload})
                if "/repository/files/" in url and "/raw" in url:
                    return "def old_func():\n    pass\n"
                if "/repository/branches" in url:
                    return {"default_branch": "main"}
                if "/merge_requests" in url:
                    return {"iid": 99, "web_url": "https://gitlab.com/test/mr/99"}
                return {}

        transport = FakeGitLabTransport()
        client = GitLabClient(transport=transport)

        result = gitlab_propose_fix(
            "group/project",
            10,
            "lib/code.py",
            "def old_func():\n    return 42\n",
            summary="return answer",
            _client_override=client,
        )

        self.assertIn("Opened merge request !99", result)
        mr_call = next(c for c in transport.calls if "/merge_requests" in c["url"])
        self.assertIn("```diff", mr_call["payload"]["description"])
        self.assertIn("+    return 42", mr_call["payload"]["description"])


if __name__ == "__main__":
    unittest.main()
