"""Visible versioning (decision 0045): reading HEAD with plain file reads."""

from pathlib import Path

from parola.buildinfo import BuildInfo, repo_head


def fake_repo(tmp_path: Path, ref: str = "refs/heads/totti",
              commit: str = "abc1234def5678") -> Path:  # fmt: skip
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(f"ref: {ref}\n")
    (git / Path(ref)).write_text(commit + "\n")
    return tmp_path


def test_head_through_a_branch_ref(tmp_path: Path):
    assert repo_head(fake_repo(tmp_path)) == "abc1234"


def test_detached_head_holds_the_hash_directly(tmp_path: Path):
    repo = fake_repo(tmp_path)
    (repo / ".git" / "HEAD").write_text("abc1234def5678\n")
    assert repo_head(repo) == "abc1234"


def test_packed_ref_is_the_fallback(tmp_path: Path):
    repo = fake_repo(tmp_path)
    (repo / ".git" / "refs" / "heads" / "totti").unlink()
    (repo / ".git" / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        "feedfacefeedface refs/heads/other\n"
        "cafebabecafebabe refs/heads/totti\n"
    )
    assert repo_head(repo) == "cafebab"


def test_no_repo_reads_as_empty(tmp_path: Path):
    assert repo_head(tmp_path) == ""


def test_staleness_only_when_both_sides_are_known(tmp_path: Path):
    repo = fake_repo(tmp_path)
    info = BuildInfo(repo)
    assert info.status() == {"engine": "abc1234", "repo": "abc1234", "stale": False}
    (repo / ".git" / "refs" / "heads" / "totti").write_text("1234567890abcdef\n")
    assert info.status() == {"engine": "abc1234", "repo": "1234567", "stale": True}
    # A repo that stops being readable never cries wolf.
    (repo / ".git" / "HEAD").unlink()
    assert info.status()["stale"] is False
