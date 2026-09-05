"""Exercises git_ops against real, throwaway local repos. A mock of
subprocess.run would only prove this code calls git with the arguments it
expects itself to call -- it would happily pass even if git's actual
--shortstat wording or %G? codes had been misunderstood. The specific bugs
worth catching here (a shortstat line missing its deletions clause, a
signature status on an unsigned commit) only show up against real git.
"""

import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
import os
from pathlib import Path

from git_vouch import git_ops


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result.stdout


class GitOpsTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="git-vouch-test-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        _git(["init", "--initial-branch=main"], cwd=self.repo)
        _git(["config", "user.email", "test@example.com"], cwd=self.repo)
        _git(["config", "user.name", "Test"], cwd=self.repo)
        # No signing key configured -- deliberately: commits in this suite
        # are unsigned by default, which is itself a case worth testing.


class CommitMessageTests(GitOpsTestCase):
    def test_returns_the_full_message_including_body(self):
        (self.repo / "a.txt").write_text("hello\n")
        _git(["add", "a.txt"], cwd=self.repo)
        _git(["commit", "-m", "Subject line", "-m", "Body paragraph."], cwd=self.repo)

        message = git_ops.commit_message("HEAD", cwd=self.repo)

        self.assertIn("Subject line", message)
        self.assertIn("Body paragraph.", message)


class CommitExistsTests(GitOpsTestCase):
    def test_true_for_a_real_commit(self):
        (self.repo / "a.txt").write_text("hello\n")
        _git(["add", "a.txt"], cwd=self.repo)
        _git(["commit", "-m", "x"], cwd=self.repo)

        self.assertTrue(git_ops.commit_exists("HEAD", cwd=self.repo))

    def test_false_for_a_nonexistent_ref(self):
        self.assertFalse(git_ops.commit_exists("not-a-real-ref", cwd=self.repo))


class HasParentTests(GitOpsTestCase):
    def test_false_for_the_root_commit(self):
        (self.repo / "a.txt").write_text("hello\n")
        _git(["add", "a.txt"], cwd=self.repo)
        _git(["commit", "-m", "root"], cwd=self.repo)

        self.assertFalse(git_ops.has_parent("HEAD", cwd=self.repo))

    def test_true_for_a_second_commit(self):
        (self.repo / "a.txt").write_text("hello\n")
        _git(["add", "a.txt"], cwd=self.repo)
        _git(["commit", "-m", "root"], cwd=self.repo)
        (self.repo / "a.txt").write_text("hello again\n")
        _git(["commit", "-am", "second"], cwd=self.repo)

        self.assertTrue(git_ops.has_parent("HEAD", cwd=self.repo))


class DiffStatTests(GitOpsTestCase):
    def test_a_single_file_addition(self):
        (self.repo / "a.txt").write_text("line1\nline2\nline3\n")
        _git(["add", "a.txt"], cwd=self.repo)
        _git(["commit", "-m", "add a.txt"], cwd=self.repo)

        # Root commit: diffed against the empty tree.
        files, insertions, deletions = git_ops.diff_stat("HEAD", cwd=self.repo)
        self.assertEqual((files, insertions, deletions), (1, 3, 0))

    def test_a_change_touching_two_files_with_both_insertions_and_deletions(self):
        (self.repo / "a.txt").write_text("line1\nline2\n")
        (self.repo / "b.txt").write_text("keep\n")
        _git(["add", "."], cwd=self.repo)
        _git(["commit", "-m", "root"], cwd=self.repo)

        (self.repo / "a.txt").write_text("line1\nchanged\nnew line\n")
        (self.repo / "b.txt").unlink()
        _git(["add", "-A"], cwd=self.repo)
        _git(["commit", "-m", "second"], cwd=self.repo)

        files, insertions, deletions = git_ops.diff_stat("HEAD", cwd=self.repo)
        self.assertEqual(files, 2)
        self.assertGreater(insertions, 0)
        self.assertGreater(deletions, 0)

    def test_a_commit_with_only_insertions_has_zero_deletions_not_a_parse_error(self):
        # Regression guard: git's --shortstat line omits the deletions
        # clause entirely when there are none, which is exactly the case
        # that breaks a parser written against only one example output.
        (self.repo / "a.txt").write_text("only additions\n")
        _git(["add", "a.txt"], cwd=self.repo)
        _git(["commit", "-m", "root"], cwd=self.repo)

        _, insertions, deletions = git_ops.diff_stat("HEAD", cwd=self.repo)
        self.assertGreater(insertions, 0)
        self.assertEqual(deletions, 0)


class SignatureStatusTests(GitOpsTestCase):
    def test_an_unsigned_commit_reports_n(self):
        (self.repo / "a.txt").write_text("hello\n")
        _git(["add", "a.txt"], cwd=self.repo)
        _git(["commit", "-m", "unsigned"], cwd=self.repo)

        self.assertEqual(git_ops.signature_status("HEAD", cwd=self.repo), "N")


class SignedCommitTests(unittest.TestCase):
    """A real GPG-signed commit, not just the unsigned case above.

    Generating a real key and making a real signed commit is the only way
    to actually exercise git's 'G' status path -- mocking %G? to return the
    letter G would prove nothing about whether git-vouch and git agree on
    what a valid signature looks like.
    """

    @classmethod
    def setUpClass(cls):
        cls.gnupg_home = Path(tempfile.mkdtemp(prefix="git-vouch-gnupg-"))
        cls.env = {**os.environ, "GNUPGHOME": str(cls.gnupg_home)}

        batch_script = cls.gnupg_home / "batch"
        batch_script.write_text(
            "%no-protection\n"
            "Key-Type: eddsa\n"
            "Key-Curve: ed25519\n"
            "Name-Real: Git Vouch Test\n"
            "Name-Email: test@example.com\n"
            "Expire-Date: 0\n"
            "%commit\n"
        )
        subprocess.run(
            ["gpg", "--batch", "--generate-key", str(batch_script)],
            env=cls.env,
            capture_output=True,
            check=True,
        )
        keys = subprocess.run(
            ["gpg", "--list-secret-keys", "--with-colons"],
            env=cls.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        cls.key_id = next(line.split(":")[4] for line in keys.splitlines() if line.startswith("sec"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.gnupg_home, ignore_errors=True)

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="git-vouch-signed-test-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        _git(["init", "--initial-branch=main"], cwd=self.repo)
        _git(["config", "user.email", "test@example.com"], cwd=self.repo)
        _git(["config", "user.name", "Git Vouch Test"], cwd=self.repo)
        _git(["config", "user.signingkey", self.key_id], cwd=self.repo)
        _git(["config", "gpg.format", "openpgp"], cwd=self.repo)

    def _git_signed(self, args: list[str]) -> None:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"git {args} failed: {result.stderr}")

    def test_a_genuinely_signed_commit_reports_g(self):
        (self.repo / "a.txt").write_text("hello\n")
        self._git_signed(["add", "a.txt"])
        self._git_signed(["commit", "-S", "-m", "signed commit"])

        # git_ops.signature_status doesn't take an env override -- it's a
        # thin wrapper meant to run in the caller's own environment, same
        # as every other git_ops function. So the test patches the process
        # environment instead of passing GNUPGHOME through a back door the
        # production code doesn't have.
        with unittest.mock.patch.dict(os.environ, {"GNUPGHOME": str(self.gnupg_home)}):
            status = git_ops.signature_status("HEAD", cwd=self.repo)

        self.assertEqual(status, "G")

    def test_amending_a_signed_commit_re_signs_it_rather_than_silently_dropping_the_signature(self):
        # Regression test for a real bug caught during manual smoke-testing:
        # `git commit --amend -m ...` without `-S` produces an *unsigned*
        # commit from a previously-signed one, unless commit.gpgsign is set
        # globally. amend_message must re-sign when the original was signed.
        (self.repo / "a.txt").write_text("hello\n")
        self._git_signed(["add", "a.txt"])
        self._git_signed(["commit", "-S", "-m", "signed commit"])

        with unittest.mock.patch.dict(os.environ, {"GNUPGHOME": str(self.gnupg_home)}):
            self.assertEqual(git_ops.signature_status("HEAD", cwd=self.repo), "G")

            git_ops.amend_message("amended message\n", cwd=self.repo)

            self.assertEqual(git_ops.signature_status("HEAD", cwd=self.repo), "G")

    def test_amending_an_unsigned_commit_does_not_force_a_signature(self):
        # The other half of the same fix: don't add -S where there was
        # never a signature to preserve, which would fail outright in an
        # environment with no signing key configured at all.
        (self.repo / "a.txt").write_text("hello\n")
        self._git_signed(["add", "a.txt"])
        self._git_signed(["commit", "-m", "unsigned commit"])  # no -S

        git_ops.amend_message("amended message\n", cwd=self.repo)

        self.assertEqual(git_ops.signature_status("HEAD", cwd=self.repo), "N")


class AmendMessageTests(GitOpsTestCase):
    def test_replaces_the_message_and_keeps_the_tree(self):
        (self.repo / "a.txt").write_text("hello\n")
        _git(["add", "a.txt"], cwd=self.repo)
        _git(["commit", "-m", "original message"], cwd=self.repo)

        git_ops.amend_message("new message\n\nAssisted-by: x\n", cwd=self.repo)

        message = git_ops.commit_message("HEAD", cwd=self.repo)
        self.assertIn("new message", message)
        self.assertIn("Assisted-by: x", message)
        self.assertNotIn("original message", message)
        # The file from the original commit is still there -- amending the
        # message must not touch the tree.
        self.assertTrue((self.repo / "a.txt").exists())


if __name__ == "__main__":
    unittest.main()
