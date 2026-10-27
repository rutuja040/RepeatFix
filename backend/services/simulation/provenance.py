"""Run provenance for simulation reports (Phase D3.3 audit remediation).

A simulation report is only reproducible if the *exact* source state that
produced it can be recovered. A bare git SHA is not enough whenever the
working tree was dirty at run time — which is the normal case in this
repository, where the D2.1 production changes and the whole D3 simulation
package are uncommitted.

This module therefore records three independent things:

1. **The git HEAD** — short/full commit, commit date and branch. This
   identifies the last committed revision, *not* necessarily the code that
   ran.
2. **The working-tree state** — whether the tree was clean, which tracked
   files were modified/staged, which paths were untracked, and a SHA-256
   digest of ``git diff HEAD``. Together with the HEAD SHA this pins the
   *committed + uncommitted* state of every tracked file.
3. **A local source digest** — the SHA-256 of every locally-imported Python
   source file that was loaded when the report was produced, plus a combined
   digest over the sorted list. This is the part that covers *untracked*
   files (the D3 simulation package itself) and therefore makes an
   uncommitted run verifiable: any edit to a file the simulation imported
   changes the digest.

The combined provenance block is labelled ``incomplete`` whenever the
working tree was not clean, because in that case the recorded commit alone
does **not** reproduce the report. The report must never claim otherwise.

Caveat: the source digest hashes the files *on disk* at digest time, not the
bytecode that was actually imported. Both are captured within the same CLI
invocation, so they agree unless a source file is edited while the run is in
flight.

Nothing here touches the database, the API or the frontend; the module only
reads the git working tree and the already-imported source files.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# backend/services/simulation/provenance.py -> repository root
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Provenance status values.
STATUS_REPRODUCIBLE = "reproducible_from_commit"
STATUS_INCOMPLETE = "incomplete"


def _run_git(args: List[str]) -> Optional[str]:
    """Run a read-only git command; ``None`` when git is unavailable.

    Only trailing newlines are removed. The output as a whole must not be
    whitespace-stripped: ``git status --porcelain`` lines for unstaged
    modifications begin with a space (``" M path"``), and stripping the
    whole buffer would eat the leading space of the *first* line and shift
    its status characters into the path.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.stdout is None:
        return None
    return result.stdout.rstrip("\r\n")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def git_head() -> Dict[str, Any]:
    """The last committed revision (not necessarily the code that ran)."""
    full = _run_git(["rev-parse", "HEAD"])
    short = _run_git(["rev-parse", "--short", "HEAD"])
    return {
        "commit": short or full,
        "commit_full": full,
        "commit_date": _run_git(["show", "-s", "--format=%cI", "HEAD"]),
        "branch": _run_git(["rev-parse", "--abbrev-ref", "HEAD"]),
    }


def git_working_tree() -> Dict[str, Any]:
    """The uncommitted state of the tree at run time."""
    porcelain = _run_git(["status", "--porcelain=v1", "--untracked-files=normal"])
    modified: List[str] = []
    staged: List[str] = []
    untracked: List[str] = []
    if porcelain:
        for line in porcelain.splitlines():
            if len(line) < 4:
                continue
            index_status = line[0]
            worktree_status = line[1]
            path = line[3:].strip().strip('"')
            if index_status == "?" and worktree_status == "?":
                untracked.append(path)
                continue
            if index_status not in (" ", ""):
                staged.append(path)
            if worktree_status not in (" ", ""):
                modified.append(path)
    diff = _run_git(["diff", "HEAD"])
    return {
        "clean": not (modified or staged or untracked),
        "modified_tracked": sorted(modified),
        "staged": sorted(staged),
        "untracked": sorted(untracked),
        "diff_head_sha256": None if diff is None else _sha256_text(diff),
    }


def local_source_digest() -> Dict[str, Any]:
    """SHA-256 of every locally-imported Python source file, plus a combined digest.

    Covers untracked modules (the D3 simulation package is untracked in this
    repository), so it is the fingerprint that actually identifies the code
    behind a report produced from a dirty working tree.
    """
    files: Dict[str, str] = {}
    for module in list(sys.modules.values()):
        raw_path = getattr(module, "__file__", None)
        if not raw_path:
            continue
        path = Path(raw_path)
        if path.suffix != ".py":
            continue
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(_REPO_ROOT)
        except (OSError, ValueError):
            continue
        try:
            files[relative.as_posix()] = _sha256_bytes(resolved.read_bytes())
        except OSError:
            continue
    combined = hashlib.sha256()
    for relative in sorted(files):
        combined.update(f"{relative}:{files[relative]}\n".encode("utf-8"))
    return {
        "algorithm": "sha256",
        "file_count": len(files),
        "combined_sha256": combined.hexdigest(),
        "files": dict(sorted(files.items())),
    }


def build_provenance() -> Dict[str, Any]:
    """The complete provenance block recorded in every simulation report.

    ``status`` is ``reproducible_from_commit`` only when the working tree
    was clean at run time *and* the HEAD commit therefore contains every
    source file the simulation used. Otherwise the status is
    ``incomplete`` and the report says so explicitly: the recorded commit
    alone does not reproduce the run, the working-tree source digest does.
    """
    head = git_head()
    tree = git_working_tree()
    digest = local_source_digest()
    clean = bool(tree["clean"]) and head["commit_full"] is not None
    uncommitted = sorted(
        set(tree["modified_tracked"]) | set(tree["staged"]) | set(tree["untracked"])
    )
    return {
        "status": STATUS_REPRODUCIBLE if clean else STATUS_INCOMPLETE,
        "reproducible_from_recorded_commit": clean,
        "head": head,
        "working_tree": tree,
        "uncommitted_paths": uncommitted,
        "source_digest": digest,
        "notes": [
            "The recorded commit identifies the last committed revision only. "
            "It reproduces this report by itself only when "
            "reproducible_from_recorded_commit is true.",
            "source_digest.combined_sha256 is the SHA-256 over the sorted "
            "'<path>:<file sha256>' lines of every locally-imported Python "
            "source file at run time; it identifies the code that actually "
            "produced the report, including untracked files.",
            "working_tree.diff_head_sha256 is the SHA-256 of 'git diff HEAD' "
            "at run time; together with the commit SHA it pins every "
            "uncommitted change to tracked files.",
        ],
    }
