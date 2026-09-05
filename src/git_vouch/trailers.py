"""Pure parsing and building of git trailers -- the `Key: value` lines at the
end of a commit message, the same mechanism `Co-authored-by:` and
`Signed-off-by:` already use. Nothing here touches git or the filesystem, so
the part where getting the parse wrong actually matters can be tested
directly, the same way beachhead's naming.py and ai-recon's patterns.py are.

git-vouch's own trailers:
  Assisted-by: <model identifier>       -- matches the informal convention
                                            already used by several projects
  Vouch-Reviewed: true|false            -- an explicit human declaration,
                                            not inferred
  Vouch-Diff-Stat: <N> files, +<A> -<D> -- computed from the actual diff,
                                            never typed by hand, so `verify`
                                            can catch drift between what was
                                            claimed and what the commit
                                            actually contains
  Vouch-Generated-At: <ISO 8601>
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VOUCH_KEYS = ("Assisted-by", "Vouch-Reviewed", "Vouch-Diff-Stat", "Vouch-Generated-At")

# A trailer line: a token made of letters/digits/hyphens, a colon, then a
# value (or nothing, for a handful of trailers git itself treats as
# valueless -- not used by git-vouch, but the parser shouldn't choke on a
# message that mixes ours with someone else's).
_TRAILER_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s?(.*)$")


@dataclass(frozen=True)
class Attestation:
    assisted_by: str | None = None
    reviewed: bool | None = None
    diff_stat: str | None = None
    generated_at: str | None = None

    @property
    def present(self) -> bool:
        """True if any git-vouch trailer was found at all."""
        return any((self.assisted_by, self.reviewed is not None, self.diff_stat, self.generated_at))


def _trailer_block(message: str) -> list[tuple[str, str]]:
    """Extracts the trailing block of `Key: value` lines from a commit message.

    Mirrors git's own trailer detection: scans backward from the end of the
    message for a contiguous run of trailer-shaped lines, stopping at the
    first line that isn't one (or at a blank line, which separates the
    trailer block from the body above it). Blank lines *within* the trailing
    run are not trailers themselves and end the scan, matching git's
    behavior for a well-formed trailer block.
    """
    lines = message.rstrip("\n").split("\n")
    trailers: list[tuple[str, str]] = []

    for line in reversed(lines):
        if line.strip() == "":
            break
        match = _TRAILER_LINE_RE.match(line)
        if not match:
            break
        trailers.append((match.group(1), match.group(2)))

    trailers.reverse()
    return trailers


def parse(message: str) -> Attestation:
    """Extracts a git-vouch Attestation from a raw commit message.

    Unrecognized trailers (someone else's `Co-authored-by:`, etc.) are
    ignored, not treated as an error -- a commit can carry trailers from
    more than one convention at once.
    """
    values: dict[str, str] = {}
    for key, value in _trailer_block(message):
        if key in VOUCH_KEYS:
            values[key] = value

    reviewed = None
    if "Vouch-Reviewed" in values:
        reviewed = values["Vouch-Reviewed"].strip().lower() == "true"

    return Attestation(
        assisted_by=values.get("Assisted-by") or None,
        reviewed=reviewed,
        diff_stat=values.get("Vouch-Diff-Stat") or None,
        generated_at=values.get("Vouch-Generated-At") or None,
    )


def build_trailer_lines(
    *,
    assisted_by: str,
    reviewed: bool,
    diff_stat: str,
    generated_at: str,
) -> list[str]:
    """Builds the trailer lines for a new attestation, in a fixed order.

    Fixed order (rather than dict-iteration order) so two runs with
    identical inputs produce byte-identical output -- useful for tests and
    for anyone diffing commit messages.
    """
    return [
        f"Assisted-by: {assisted_by}",
        f"Vouch-Reviewed: {'true' if reviewed else 'false'}",
        f"Vouch-Diff-Stat: {diff_stat}",
        f"Vouch-Generated-At: {generated_at}",
    ]


def format_diff_stat(files_changed: int, insertions: int, deletions: int) -> str:
    """Formats a diffstat the same way for both generation and comparison.

    Both `generate` and `verify` must produce byte-identical strings from
    the same underlying numbers, or verify's drift check would false-flag
    every attestation.
    """
    noun = "file" if files_changed == 1 else "files"
    return f"{files_changed} {noun} changed, +{insertions} -{deletions}"
