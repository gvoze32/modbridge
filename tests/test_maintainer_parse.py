from modbridge.adapters.maintainer import parse_change_line, parse_update_log

SAMPLE_LOG = """\
[2026-07-09 04:00:01] Info | Checking for updates
[2026-07-09 04:00:05] Update | Sodium 0.5.8 -> 0.6.0
[2026-07-09 04:00:06] Update | Minecraft 1.21.0 -> 1.21.1
[2026-07-09 04:00:08] ERROR | Download failed for Lithium
not a log line
"""

ROLLBACK_LOG = """\
[2026-07-09 04:00:01] WARN | Previous update interrupted - rolling back
[2026-07-09 04:00:02] Info | Restoring backup...
[2026-07-09 04:00:09] OK | Restored from backup
"""


def test_parse_update_log() -> None:
    applied, errors, rolled_back = parse_update_log(SAMPLE_LOG)
    assert [(c.name, c.old_version, c.new_version) for c in applied] == [
        ("Sodium", "0.5.8", "0.6.0"),
        ("Minecraft", "1.21.0", "1.21.1"),
    ]
    assert errors == ["Download failed for Lithium"]
    assert rolled_back is False


def test_parse_rollback_markers() -> None:
    _, _, rolled_back = parse_update_log(ROLLBACK_LOG)
    assert rolled_back is True


def test_parse_change_line_unicode_arrow() -> None:
    change = parse_change_line("Fabric API 0.100.1 → 0.100.4")
    assert change is not None
    assert change.name == "Fabric API"
    assert change.new_version == "0.100.4"


def test_parse_change_line_rejects_prose() -> None:
    assert parse_change_line("Checking for updates") is None
    assert parse_change_line("3 to update") is None
