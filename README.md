# git-vouch

Verifiable attestation for AI-assisted git commits.

```
$ git-vouch generate --model claude-sonnet-5 --reviewed true --amend
Assisted-by: claude-sonnet-5
Vouch-Reviewed: true
Vouch-Diff-Stat: 1 file changed, +1 -0
Vouch-Generated-At: 2026-09-05T09:00:32Z

$ git-vouch verify
Checking HEAD
Assisted-by: claude-sonnet-5
Reviewed by a human: True
Claimed diff stat:  1 file changed, +1 -0
Actual diff stat:   matches claimed stat
Commit signature:   good signature

Verdict: attestation present, diff stat matches, commit signature good.
```

## Why this exists

More repos are asking for AI-assistance disclosure — a note in the PR
description, an `Assisted-by` trailer, a checkbox — but disclosure today is
just prose. Nothing stops it from being wrong, stale, or simply untrue, and
nothing lets a reviewer check it without re-reading the whole diff by hand.

`git-vouch` doesn't invent a new trust mechanism. It uses two things git
already has:

- **Trailers** — the same `Key: value` convention `Co-authored-by:` and
  `Signed-off-by:` already use, so the attestation is visible right in the
  commit message, not hidden in a side-channel `git notes` ref that GitHub's
  PR view won't show you.
- **Commit signatures** — if you already sign your commits (or git-vouch's
  `generate --amend` will re-sign one for you rather than silently stripping
  an existing signature, which plain `git commit --amend` does), the
  attestation trailer is protected by that same signature. No new crypto,
  no new keys to manage.

The one thing git-vouch adds: the diff stat in the trailer is **computed
from the actual commit**, never typed by hand. `verify` recomputes it and
compares, so a trailer that's been hand-edited after the fact — to claim a
smaller, more innocent-looking change than what's actually in the commit —
gets caught, not trusted.

## Install

```bash
pip install git-vouch
```

Requires Python 3.10+ and `git` on `PATH`.

## Use

```bash
# After making a commit:
git-vouch generate --model claude-sonnet-5 --reviewed true --amend

# Anyone reviewing later:
git-vouch verify              # checks HEAD
git-vouch verify <commit-ish> # checks any commit
```

Without `--amend`, `generate` prints the trailer block to stdout instead of
touching anything, so you can paste it into your own commit message or pipe
it elsewhere.

`generate` refuses to overwrite an existing attestation — amend the commit
yourself first if you need to replace one.

## What `verify` actually checks

1. **Is there an attestation at all?** If not, says so plainly.
2. **Does the claimed diff stat match the actual commit?** Recomputed from
   `git diff --shortstat`, not read from the trailer's own claim.
3. **Is the commit signed, and is the signature good?** Uses git's own
   `%G?` status codes — the same ones `git log --show-signature` shows you.

An attestation with a matching diff stat and a good signature is the strong
case. An unsigned commit gets an explicit caveat: the trailer text could
have been added by anyone, at any time, since nothing is protecting it.

## What this deliberately is not

git-vouch doesn't verify that a human actually reviewed the change, only
that someone *claimed* they did (`Vouch-Reviewed: true`). It doesn't
identify which specific model produced the diff — `Assisted-by` is
free text, exactly like `Co-authored-by` already is. And an unsigned
attestation is exactly as trustworthy as an unsigned commit already is:
better than nothing, not proof of anything.

## Why zero dependencies

Like [beachhead](https://github.com/tusharnagar2002/beachhead) and
[ai-recon](https://github.com/tusharnagar2002/ai-recon), this imports only
the Python standard library and shells out to `git` directly.

## Contributing

The test suite mirrors the same layered approach: pure trailer parsing
(`trailers.py`) is tested directly with no I/O; `git_ops.py` and `cli.py`
are tested against real, throwaway local git repos — including a real
GPG-signed commit made with a freshly generated test key, not a mocked
signature status. That real signing test caught a genuine bug during
development: `git commit --amend` without `-S` silently drops an existing
signature, which would have made every attested commit *less* verifiable
than before it was attested. Fixed, with a regression test.

```bash
git clone https://github.com/tusharnagar2002/git-vouch.git
cd git-vouch
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
```

## License

MIT
