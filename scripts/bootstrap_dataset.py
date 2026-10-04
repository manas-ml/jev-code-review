"""Best-effort: bootstrap eval examples from merged PRs where a human review comment was
followed by a code change to that file in a later commit.

Usage: GITHUB_TOKEN=... uv run python scripts/bootstrap_dataset.py OWNER/REPO PR_NUMBER...

Heuristic and its limits (see README): a label is created for each inline review comment on
file F (line L) if a commit authored AFTER the comment touches F. This overcounts (unrelated
later edits), undercounts (fixes in other files, or discussed-then-declined), and treats
every such comment as a defect even when it was a style request. Review the output by hand.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

API = "https://api.github.com"


def main(repo: str, numbers: list[str], out: Path = Path("eval/dataset")) -> None:
    h = {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }
    with httpx.Client(headers=h, timeout=30) as c:
        for n in numbers:
            pr = c.get(f"{API}/repos/{repo}/pulls/{n}").json()
            if not pr.get("merged_at"):
                print(f"skip #{n}: not merged")
                continue
            diff = c.get(
                f"{API}/repos/{repo}/pulls/{n}",
                headers={**h, "Accept": "application/vnd.github.diff"},
            ).text
            comments = c.get(
                f"{API}/repos/{repo}/pulls/{n}/comments", params={"per_page": 100}
            ).json()
            commits = c.get(
                f"{API}/repos/{repo}/pulls/{n}/commits", params={"per_page": 100}
            ).json()
            touched: list[tuple[str, set[str]]] = []
            for cm in commits:
                files = c.get(f"{API}/repos/{repo}/commits/{cm['sha']}").json().get("files", [])
                touched.append((cm["commit"]["committer"]["date"], {f["filename"] for f in files}))
            issues = []
            for cm in comments:
                if any(d > cm["created_at"] and cm["path"] in fs for d, fs in touched):
                    line = cm.get("line") or cm.get("original_line") or 1
                    issues.append(
                        {
                            "file": cm["path"],
                            "line_start": line,
                            "line_end": line,
                            "description": cm["body"][:300],
                        }
                    )
            d = out / f"{repo.replace('/', '__')}__{n}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "diff.patch").write_text(diff)
            (d / "meta.json").write_text(
                json.dumps({"title": pr["title"], "description": pr.get("body") or ""}, indent=2)
            )
            (d / "labels.json").write_text(json.dumps({"issues": issues, "nitpicks": []}, indent=2))
            print(f"#{n}: {len(issues)} candidate labels -> {d}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
