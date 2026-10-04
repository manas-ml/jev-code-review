"""Minimal GitHub REST client: fetch a PR and (optionally) post a review."""

from __future__ import annotations

import re

import httpx

from codereview.context.builder import build_context
from codereview.models import ExistingComment, ReviewContext

_PR_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")


def parse_pr_url(url: str) -> tuple[str, str, int]:
    m = _PR_URL.search(url)
    if not m:
        raise ValueError(f"not a GitHub PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def _headers(token: str, accept: str = "application/vnd.github+json") -> dict[str, str]:
    h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def fetch_pr(url: str, token: str, repo_root: str | None = None) -> ReviewContext:
    owner, repo, num = parse_pr_url(url)
    base = f"https://api.github.com/repos/{owner}/{repo}"
    async with httpx.AsyncClient(timeout=30) as http:
        meta = await http.get(f"{base}/pulls/{num}", headers=_headers(token))
        meta.raise_for_status()
        diff = await http.get(
            f"{base}/pulls/{num}", headers=_headers(token, "application/vnd.github.diff")
        )
        diff.raise_for_status()
        comments = await http.get(
            f"{base}/pulls/{num}/comments", headers=_headers(token), params={"per_page": 100}
        )
        comments.raise_for_status()
    m = meta.json()
    existing = [
        ExistingComment(file=c["path"], line=c.get("line"), body=c["body"]) for c in comments.json()
    ]
    return build_context(
        pr_id=f"{owner}__{repo}__{num}",
        title=m["title"],
        description=m.get("body") or "",
        diff=diff.text,
        existing_comments=existing,
        repo_root=repo_root,
    )


async def post_review(url: str, token: str, body: str, comments: list[dict[str, object]]) -> None:
    owner, repo, num = parse_pr_url(url)
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{num}/reviews",
            headers=_headers(token),
            json={"body": body, "event": "COMMENT", "comments": comments},
        )
        r.raise_for_status()
