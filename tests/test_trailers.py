import unittest

from git_vouch.trailers import Attestation, build_trailer_lines, format_diff_stat, parse


class ParseTests(unittest.TestCase):
    def test_parses_a_full_attestation(self):
        message = (
            "Fix the login bug\n"
            "\n"
            "Longer body explaining the fix.\n"
            "\n"
            "Assisted-by: claude-sonnet-5\n"
            "Vouch-Reviewed: true\n"
            "Vouch-Diff-Stat: 2 files changed, +10 -3\n"
            "Vouch-Generated-At: 2026-09-05T10:00:00Z\n"
        )
        result = parse(message)

        self.assertEqual(result.assisted_by, "claude-sonnet-5")
        self.assertTrue(result.reviewed)
        self.assertEqual(result.diff_stat, "2 files changed, +10 -3")
        self.assertEqual(result.generated_at, "2026-09-05T10:00:00Z")
        self.assertTrue(result.present)

    def test_reviewed_false_is_parsed_as_false_not_falsy_absence(self):
        message = "Subject\n\nAssisted-by: x\nVouch-Reviewed: false\n"
        result = parse(message)

        self.assertFalse(result.reviewed)
        self.assertIsNotNone(result.reviewed)  # False, not None -- a real claim either way

    def test_reviewed_is_case_insensitive(self):
        message = "Subject\n\nVouch-Reviewed: TRUE\n"
        self.assertTrue(parse(message).reviewed)

    def test_no_trailers_at_all_gives_an_empty_not_present_attestation(self):
        message = "Just a normal commit message.\n\nWith a body.\n"
        result = parse(message)

        self.assertFalse(result.present)
        self.assertIsNone(result.assisted_by)
        self.assertIsNone(result.reviewed)

    def test_a_message_with_only_other_peoples_trailers_is_not_present(self):
        message = "Subject\n\nCo-authored-by: Someone <someone@example.com>\nSigned-off-by: Someone\n"
        result = parse(message)

        self.assertFalse(result.present)

    def test_git_vouch_trailers_mixed_with_other_trailers(self):
        message = (
            "Subject\n"
            "\n"
            "Co-authored-by: Someone <someone@example.com>\n"
            "Assisted-by: claude-sonnet-5\n"
            "Vouch-Reviewed: true\n"
        )
        result = parse(message)

        self.assertEqual(result.assisted_by, "claude-sonnet-5")
        self.assertTrue(result.reviewed)

    def test_a_blank_line_inside_the_trailing_run_ends_the_trailer_block(self):
        # Mirrors git's own trailer detection: a blank line separates the
        # trailer block from whatever's above it, including from a second,
        # earlier-looking trailer-shaped line that isn't actually part of
        # the block.
        message = "Subject\n\nAssisted-by: should-not-be-found\n\nVouch-Reviewed: true\n"
        result = parse(message)

        self.assertIsNone(result.assisted_by)
        self.assertTrue(result.reviewed)

    def test_a_body_line_that_happens_to_contain_a_colon_does_not_break_parsing(self):
        message = (
            "Subject\n"
            "\n"
            "See https://example.com: it explains the bug.\n"
            "\n"
            "Assisted-by: claude-sonnet-5\n"
        )
        result = parse(message)

        self.assertEqual(result.assisted_by, "claude-sonnet-5")

    def test_empty_message(self):
        self.assertFalse(parse("").present)

    def test_subject_only_no_trailers(self):
        self.assertFalse(parse("Just a subject line").present)

    def test_a_trailer_shaped_subject_line_alone_is_still_parsed(self):
        # Edge case: a one-line commit message that happens to look like a
        # trailer. git itself has the same ambiguity for a single-line
        # message; git-vouch matches that behavior rather than special-casing it.
        result = parse("Assisted-by: claude-sonnet-5")
        self.assertEqual(result.assisted_by, "claude-sonnet-5")


class BuildTrailerLinesTests(unittest.TestCase):
    def test_builds_all_four_lines_in_a_fixed_order(self):
        lines = build_trailer_lines(
            assisted_by="claude-sonnet-5",
            reviewed=True,
            diff_stat="2 files changed, +10 -3",
            generated_at="2026-09-05T10:00:00Z",
        )
        self.assertEqual(
            lines,
            [
                "Assisted-by: claude-sonnet-5",
                "Vouch-Reviewed: true",
                "Vouch-Diff-Stat: 2 files changed, +10 -3",
                "Vouch-Generated-At: 2026-09-05T10:00:00Z",
            ],
        )

    def test_reviewed_false_renders_as_the_literal_string_false(self):
        lines = build_trailer_lines(
            assisted_by="x", reviewed=False, diff_stat="1 file changed, +1 -0", generated_at="t"
        )
        self.assertIn("Vouch-Reviewed: false", lines)

    def test_round_trips_through_parse(self):
        lines = build_trailer_lines(
            assisted_by="claude-sonnet-5",
            reviewed=True,
            diff_stat="3 files changed, +20 -5",
            generated_at="2026-09-05T10:00:00Z",
        )
        message = "Subject\n\n" + "\n".join(lines) + "\n"
        result = parse(message)

        self.assertEqual(result.assisted_by, "claude-sonnet-5")
        self.assertTrue(result.reviewed)
        self.assertEqual(result.diff_stat, "3 files changed, +20 -5")
        self.assertEqual(result.generated_at, "2026-09-05T10:00:00Z")


class FormatDiffStatTests(unittest.TestCase):
    def test_pluralizes_files_correctly(self):
        self.assertEqual(format_diff_stat(1, 5, 2), "1 file changed, +5 -2")
        self.assertEqual(format_diff_stat(2, 5, 2), "2 files changed, +5 -2")
        self.assertEqual(format_diff_stat(0, 0, 0), "0 files changed, +0 -0")

    def test_generate_and_verify_paths_must_produce_identical_strings(self):
        # Not a real regression by itself, but documents the invariant that
        # every other test in this file relies on: there is exactly one
        # function that turns numbers into a diffstat string, used by both
        # `generate` and `verify`, so they can never drift from each other.
        a = format_diff_stat(3, 12, 4)
        b = format_diff_stat(3, 12, 4)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
