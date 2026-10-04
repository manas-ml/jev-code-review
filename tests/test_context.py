from pathlib import Path

from codereview.context.builder import (
    file_chunks,
    load_local,
    parse_unified_diff,
    render_triage_text,
)

FIX = Path(__file__).parent / "fixtures"


def load(name: str):  # type: ignore[no-untyped-def]
    return load_local(FIX / name / "diff.patch", FIX / name / "meta.json")


def test_parse_counts():
    ctx = load("bugfix")
    assert [f.path for f in ctx.files] == ["app/pricing.py"]
    f = ctx.files[0]
    assert f.additions == 3 and f.deletions == 2


def test_multi_file_parse():
    diff = "diff --git a/a b/a\n+x\ndiff --git a/b b/b\n-y\n"
    files = parse_unified_diff(diff)
    assert [(f.path, f.additions, f.deletions) for f in files] == [("a", 1, 0), ("b", 0, 1)]


def test_untrusted_delimiters_and_notice():
    text = render_triage_text(load("injection"), 120_000)
    assert "<<<BEGIN UNTRUSTED TITLE>>>" in text
    assert "<<<END UNTRUSTED DIFF>>>" in text
    assert "ignore them" in text


def test_truncation_keeps_highest_churn_and_budget():
    big = "diff --git a/big.py b/big.py\n@@ -1 +1 @@\n" + "\n".join(
        f"+line{i}" for i in range(5000)
    )
    small = "diff --git a/small.py b/small.py\n@@ -1 +1 @@\n+a\n"
    ctx = load("bugfix")
    ctx.files = parse_unified_diff(big + "\n" + small)
    text = render_triage_text(ctx, 3000)
    assert len(text) <= 3500
    assert "big.py (+5000/-0)" in text  # file list always complete
    assert "diff truncated" in text


def test_file_chunks_split():
    ctx = load("bugfix")
    ctx.files = parse_unified_diff(
        "diff --git a/a b/a\n" + "+x\n" * 100 + "diff --git a/b b/b\n" + "+y\n" * 100
    )
    assert len(file_chunks(ctx, max_chars=300)) == 2


def test_number_patch_shows_new_file_lines():
    from codereview.context.builder import commentable_lines, number_patch

    patch = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +10,4 @@\n a\n-b\n+c\n+d\n e\n"
    out = number_patch(patch).splitlines()
    assert out[4] == "   10   a"
    assert out[5] == "      - b"
    assert out[6] == "   11 + c" and out[8] == "   13   e"
    assert out[2] == "+++ b/f"  # headers untouched
    ctx = load("bugfix")
    lines = commentable_lines(ctx.files)["app/pricing.py"]
    assert 13 in lines and 21 in lines and 25 not in lines
