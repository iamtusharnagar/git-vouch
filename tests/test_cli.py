import contextlib
import datetime
import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from git_vouch import cli


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result.stdout


class VouchTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="git-vouch-cli-test-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        _git(["init", "--initial-branch=main"], cwd=self.repo)
        _git(["config", "user.email", "test@example.com"], cwd=self.repo)
        _git(["config", "user.name", "Test"], cwd=self.repo)
        (self.repo / "a.txt").write_text("line1\nline2\n")
        _git(["add", "a.txt"], cwd=self.repo)
        _git(["commit", "-m", "add a.txt"], cwd=self.repo)


class GenerateTests(VouchTestCase):
    def test_produces_the_right_trailer_block_without_amending(self):
        block = cli.generate(
            assisted_by="claude-sonnet-5",
            reviewed=True,
            cwd=self.repo,
            now=datetime.datetime(2026, 9, 5, 10, 0, 0, tzinfo=datetime.timezone.utc),
        )

        self.assertIn("Assisted-by: claude-sonnet-5", block)
        self.assertIn("Vouch-Reviewed: true", block)
        self.assertIn("Vouch-Diff-Stat: 1 file changed, +2 -0", block)
        self.assertIn("Vouch-Generated-At: 2026-09-05T10:00:00Z", block)

        # Without --amend, the commit itself must be untouched.
        message = _git(["log", "-1", "--format=%B"], cwd=self.repo)
        self.assertNotIn("Assisted-by", message)

    def test_amend_actually_rewrites_head(self):
        cli.generate(assisted_by="claude-sonnet-5", reviewed=True, cwd=self.repo, amend=True)

        message = _git(["log", "-1", "--format=%B"], cwd=self.repo)
        self.assertIn("Assisted-by: claude-sonnet-5", message)
        self.assertIn("add a.txt", message)  # original subject preserved
        # The tree must be untouched by amending the message.
        self.assertEqual((self.repo / "a.txt").read_text(), "line1\nline2\n")

    def test_amend_refuses_to_overwrite_an_existing_attestation(self):
        cli.generate(assisted_by="claude-sonnet-5", reviewed=True, cwd=self.repo, amend=True)

        with self.assertRaises(cli.VouchError):
            cli.generate(assisted_by="a-different-model", reviewed=False, cwd=self.repo, amend=True)

    def test_amend_on_a_ref_other_than_head_is_rejected(self):
        with self.assertRaises(cli.VouchError):
            cli.generate(assisted_by="x", reviewed=True, cwd=self.repo, ref="HEAD~0^{commit}", amend=True)
        # Note: HEAD~0 resolves to the same commit as HEAD, but the check is
        # on the literal ref string, not what it resolves to -- amend is
        # only ever allowed via the literal "HEAD" spelling, deliberately
        # conservative rather than trying to detect equivalence.

    def test_generate_on_a_nonexistent_ref_raises(self):
        with self.assertRaises(cli.VouchError):
            cli.generate(assisted_by="x", reviewed=True, cwd=self.repo, ref="not-a-real-ref")


class VerifyTests(VouchTestCase):
    def test_no_attestation_present(self):
        attestation, details = cli.verify(cwd=self.repo)

        self.assertFalse(attestation.present)

    def test_a_matching_attestation_verifies_clean(self):
        cli.generate(assisted_by="claude-sonnet-5", reviewed=True, cwd=self.repo, amend=True)

        attestation, details = cli.verify(cwd=self.repo)

        self.assertTrue(attestation.present)
        self.assertTrue(details["diff_stat_matches"])
        self.assertEqual(details["signature_status"], "N")  # unsigned in this test repo

    def test_a_tampered_diff_stat_is_caught_as_a_mismatch(self):
        cli.generate(assisted_by="claude-sonnet-5", reviewed=True, cwd=self.repo, amend=True)
        # Simulate tampering: hand-edit the message to claim a bigger diff
        # than actually happened, the way a dishonest trailer typed by hand
        # (rather than generated) could.
        message = _git(["log", "-1", "--format=%B"], cwd=self.repo)
        tampered = message.replace("1 file changed, +2 -0", "10 files changed, +500 -200")
        _git(["commit", "--amend", "-m", tampered], cwd=self.repo)

        attestation, details = cli.verify(cwd=self.repo)

        self.assertTrue(attestation.present)
        self.assertFalse(details["diff_stat_matches"])

    def test_verify_on_a_nonexistent_ref_raises(self):
        with self.assertRaises(cli.VouchError):
            cli.verify(ref="not-a-real-ref", cwd=self.repo)


class FormatVerifyResultTests(unittest.TestCase):
    def test_no_attestation_message(self):
        from git_vouch.trailers import Attestation

        text = cli._format_verify_result("HEAD", Attestation(), {})
        self.assertIn("No git-vouch attestation found", text)

    def test_matching_signed_attestation_gets_a_clean_verdict(self):
        from git_vouch.trailers import Attestation

        attestation = Attestation(
            assisted_by="claude-sonnet-5",
            reviewed=True,
            diff_stat="1 file changed, +2 -0",
            generated_at="2026-09-05T10:00:00Z",
        )
        details = {
            "signature_status": "G",
            "actual_diff_stat": "1 file changed, +2 -0",
            "diff_stat_matches": True,
        }

        text = cli._format_verify_result("HEAD", attestation, details)

        self.assertIn("Verdict: attestation present, diff stat matches, commit signature good.", text)

    def test_unsigned_gets_a_specific_caveat_not_a_generic_one(self):
        from git_vouch.trailers import Attestation

        attestation = Attestation(assisted_by="x", reviewed=True, diff_stat="1 file changed, +1 -0")
        details = {
            "signature_status": "N",
            "actual_diff_stat": "1 file changed, +1 -0",
            "diff_stat_matches": True,
        }

        text = cli._format_verify_result("HEAD", attestation, details)

        self.assertIn("the commit is unsigned", text)

    def test_mismatched_diff_stat_gets_flagged_explicitly(self):
        from git_vouch.trailers import Attestation

        attestation = Attestation(assisted_by="x", reviewed=True, diff_stat="1 file changed, +1 -0")
        details = {
            "signature_status": "G",
            "actual_diff_stat": "5 files changed, +50 -10",
            "diff_stat_matches": False,
        }

        text = cli._format_verify_result("HEAD", attestation, details)

        self.assertIn("DOES NOT MATCH", text)
        self.assertIn("unverifiable", text)


class MainTests(VouchTestCase):
    def test_generate_prints_and_exits_0(self):
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with _chdir(self.repo):
                exit_code = cli.main(["generate", "--model", "claude-sonnet-5", "--reviewed", "true"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Assisted-by: claude-sonnet-5", out.getvalue())

    def test_verify_prints_and_exits_0(self):
        import io

        with _chdir(self.repo):
            cli.main(["generate", "--model", "x", "--reviewed", "true", "--amend"])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exit_code = cli.main(["verify"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Assisted-by: x", out.getvalue())

    def test_bad_ref_exits_1(self):
        with _chdir(self.repo):
            with unittest.mock.patch("sys.stderr"):
                exit_code = cli.main(["verify", "not-a-real-ref"])
        self.assertEqual(exit_code, 1)


@contextlib.contextmanager
def _chdir(path: Path):
    original = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(original)


if __name__ == "__main__":
    unittest.main()
