"""Git subprocess wrappers. Every function is one git invocation with a clear
failure mode. `run` is the seam a test could patch, but the test suite for
this module deliberately does not patch it -- see tests/test_git_ops.py's
module docstring for why a mock would prove nothing useful here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    def __init__(self, command: list[str], returncode: int, stderr: str):
        super().__init__(f"`{' '.join(command)}` exited {returncode}: {stderr.strip()}")
        self.command = command
        self.returncode = returncode
        self.stderr = stderr


def run(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitError(["git", *args], result.returncode, result.stderr)
    return result.stdout


def commit_message(ref: str, cwd: Path | None = None) -> str:
    """Returns the full, raw commit message (subject + body + trailers) for `ref`."""
    return run(["log", "-1", "--format=%B", ref], cwd=cwd)


def commit_exists(ref: str, cwd: Path | None = None) -> bool:
    try:
        run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=cwd)
        return True
    except GitError:
        return False


def has_parent(ref: str, cwd: Path | None = None) -> bool:
    """Whether `ref` has a first parent -- false for a repo's very first commit."""
    try:
        run(["rev-parse", "--verify", "--quiet", f"{ref}^"], cwd=cwd)
        return True
    except GitError:
        return False


def diff_stat(ref: str, cwd: Path | None = None) -> tuple[int, int, int]:
    """Returns (files_changed, insertions, deletions) for `ref` against its
    first parent. For a root commit with no parent, diffs against the empty
    tree instead, so the very first commit in a repo can still be vouched for.
    """
    base = f"{ref}^" if has_parent(ref, cwd) else _EMPTY_TREE_SHA
    output = run(["diff", "--shortstat", base, ref], cwd=cwd)
    return _parse_shortstat(output)


# The well-known SHA-1 of git's empty tree object, valid in every git repo
# without needing to be created first.
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _parse_shortstat(output: str) -> tuple[int, int, int]:
    """Parses git's `--shortstat` line, e.g. '3 files changed, 12 insertions(+), 4 deletions(-)'.

    Any of the three clauses can be absent (a commit that only inserts has
    no 'deletions' clause at all), and a change touching exactly one file
    or one line uses the singular ('1 file changed', '1 insertion(+)').
    """
    files = insertions = deletions = 0
    output = output.strip()
    if not output:
        return (0, 0, 0)

    for part in output.split(","):
        part = part.strip()
        if "file" in part:
            files = int(part.split()[0])
        elif "insertion" in part:
            insertions = int(part.split()[0])
        elif "deletion" in part:
            deletions = int(part.split()[0])

    return (files, insertions, deletions)


def signature_status(ref: str, cwd: Path | None = None) -> str:
    """Returns git's one-character signature status code for `ref`.

    G = good signature, B = bad signature, U = good signature, unknown
    validity, X = good signature, expired, Y = good signature, expired key,
    R = good signature, revoked key, E = signature could not be checked
    (e.g. missing public key), N = no signature at all.
    """
    return run(["log", "-1", "--format=%G?", ref], cwd=cwd).strip()


def amend_message(new_message: str, cwd: Path | None = None) -> None:
    """Amends HEAD's commit message in place, keeping everything else (author,
    tree, timestamps handled by git's own --amend semantics) unchanged.

    Re-signs the amended commit if the original was signed. `git commit
    --amend` does NOT do this on its own: without `-S`, amending a signed
    commit silently produces an *unsigned* one unless commit.gpgsign is set
    globally. For a tool whose entire purpose is a verifiable attestation,
    silently dropping the signature on the very commit it's attesting would
    defeat the point.
    """
    was_signed = signature_status("HEAD", cwd=cwd) != "N"
    args = ["commit", "--amend", "-m", new_message]
    if was_signed:
        args.insert(2, "-S")
    run(args, cwd=cwd)
