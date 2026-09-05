"""The `git-vouch` command: attach and check verifiable AI-assistance
attestations on git commits.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from . import git_ops
from .trailers import Attestation, build_trailer_lines, format_diff_stat, parse

_SIGNATURE_MEANINGS = {
    "G": "good signature",
    "B": "BAD signature",
    "U": "good signature, signer's identity not certified",
    "X": "good signature, but expired",
    "Y": "good signature, made with an expired key",
    "R": "good signature, made with a revoked key",
    "E": "signature could not be checked (e.g. public key unavailable)",
    "N": "no signature",
}


class VouchError(RuntimeError):
    """Raised for any git-vouch-specific failure with a user-facing message."""


def generate(
    *,
    assisted_by: str,
    reviewed: bool,
    ref: str = "HEAD",
    cwd: Path | None = None,
    amend: bool = False,
    now: datetime.datetime | None = None,
) -> str:
    """Builds an attestation trailer block for `ref` and returns it as text.

    If `amend` is set, also rewrites `ref`'s commit message in place to
    include it (only ever HEAD -- amending anything else would rewrite
    history out from under other commits, which git-vouch has no business
    doing on your behalf).
    """
    if not git_ops.commit_exists(ref, cwd=cwd):
        raise VouchError(f"no such commit: {ref}")

    files, insertions, deletions = git_ops.diff_stat(ref, cwd=cwd)
    diff_stat_text = format_diff_stat(files, insertions, deletions)
    timestamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = build_trailer_lines(
        assisted_by=assisted_by,
        reviewed=reviewed,
        diff_stat=diff_stat_text,
        generated_at=timestamp,
    )
    block = "\n".join(lines)

    if amend:
        if ref != "HEAD":
            raise VouchError("--amend only works on HEAD, to avoid rewriting other history")
        original = git_ops.commit_message(ref, cwd=cwd).rstrip("\n")
        existing = parse(original)
        if existing.present:
            raise VouchError(
                "HEAD already has a git-vouch attestation. Amend the commit yourself "
                "to replace it, then re-run generate -- git-vouch won't silently "
                "overwrite an existing attestation."
            )
        new_message = f"{original}\n\n{block}\n"
        git_ops.amend_message(new_message, cwd=cwd)

    return block


def verify(ref: str = "HEAD", cwd: Path | None = None) -> tuple[Attestation, dict]:
    """Checks `ref`'s attestation (if any) against the commit's actual diff
    and signature status. Returns the parsed attestation plus a details dict
    the CLI formats for display -- kept separate from formatting so this
    stays usable as a library, not just a CLI.
    """
    if not git_ops.commit_exists(ref, cwd=cwd):
        raise VouchError(f"no such commit: {ref}")

    message = git_ops.commit_message(ref, cwd=cwd)
    attestation = parse(message)

    details: dict = {"signature_status": git_ops.signature_status(ref, cwd=cwd)}

    if attestation.present and attestation.diff_stat is not None:
        files, insertions, deletions = git_ops.diff_stat(ref, cwd=cwd)
        actual = format_diff_stat(files, insertions, deletions)
        details["actual_diff_stat"] = actual
        details["diff_stat_matches"] = actual == attestation.diff_stat

    return attestation, details


def _format_verify_result(ref: str, attestation: Attestation, details: dict) -> str:
    lines = [f"Checking {ref}"]

    if not attestation.present:
        lines.append("No git-vouch attestation found on this commit.")
        return "\n".join(lines)

    lines.append(f"Assisted-by: {attestation.assisted_by or '(not stated)'}")
    lines.append(f"Reviewed by a human: {attestation.reviewed}")
    lines.append(f"Claimed diff stat:  {attestation.diff_stat}")

    if "actual_diff_stat" in details:
        if details["diff_stat_matches"]:
            lines.append("Actual diff stat:   matches claimed stat")
        else:
            lines.append(f"Actual diff stat:   {details['actual_diff_stat']} -- DOES NOT MATCH claim")
            lines.append(
                "This attestation is stale or was written for a different diff. "
                "Treat it as unverifiable."
            )

    sig = details["signature_status"]
    lines.append(f"Commit signature:   {_SIGNATURE_MEANINGS.get(sig, sig)}")

    if sig == "G" and details.get("diff_stat_matches", False):
        lines.append("")
        lines.append("Verdict: attestation present, diff stat matches, commit signature good.")
    elif sig == "N":
        lines.append("")
        lines.append(
            "Verdict: attestation present but the commit is unsigned -- the trailer "
            "text could have been added by anyone, including after the fact."
        )
    else:
        lines.append("")
        lines.append("Verdict: attestation present but not fully verifiable. See details above.")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-vouch",
        description="Attach and check verifiable AI-assistance attestations on git commits.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser("generate", help="build an attestation for a commit")
    gen.add_argument("--model", required=True, help="model or agent identifier, e.g. claude-sonnet-5")
    gen.add_argument(
        "--reviewed",
        required=True,
        choices=["true", "false"],
        help="whether a human reviewed this change before it was committed",
    )
    gen.add_argument("--ref", default="HEAD", help="commit to attest (default: HEAD)")
    gen.add_argument(
        "--amend", action="store_true", help="amend the commit's message in place (HEAD only)"
    )

    ver = subparsers.add_parser("verify", help="check a commit's attestation")
    ver.add_argument("ref", nargs="?", default="HEAD", help="commit to check (default: HEAD)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "generate":
            block = generate(
                assisted_by=args.model,
                reviewed=args.reviewed == "true",
                ref=args.ref,
                amend=args.amend,
            )
            print(block)
            if args.amend:
                print("\n(commit message amended)", file=sys.stderr)
        elif args.command == "verify":
            attestation, details = verify(args.ref)
            print(_format_verify_result(args.ref, attestation, details))
    except (VouchError, git_ops.GitError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
