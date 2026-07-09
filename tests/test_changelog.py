from datetime import datetime

from modbridge.changelog.renderer import render_changelog
from modbridge.domain.models import ChangeKind, ChangeSet, ModChange


def change(
    kind: ChangeKind, name: str, old: str | None = None, new: str | None = None
) -> ModChange:
    return ModChange(
        kind=kind, display_name=name, filename=f"{name.lower()}.jar",
        old_version=old, new_version=new,
    )


def test_render_full_changelog() -> None:
    cs = ChangeSet(
        changes=(
            change(ChangeKind.UPDATED, "Sodium", "0.5", "0.6"),
            change(ChangeKind.ADDED, "Lithium", new="1.0"),
            change(ChangeKind.REMOVED, "OldMod", old="0.1"),
        ),
        minecraft_old="1.21.0",
        minecraft_new="1.21.1",
    )
    text = render_changelog(
        cs, version="2026.07.09", title_format="Server Update {version}",
        now=datetime(2026, 7, 9, 4, 30),
    )
    assert "# Server Update 2026.07.09" in text
    assert "**Minecraft:** 1.21.0 → 1.21.1" in text
    assert "## Updated" in text and "**Sodium**: 0.5 → 0.6" in text
    assert "## Added" in text and "**Lithium** (1.0)" in text
    assert "## Removed" in text and "**OldMod**" in text


def test_render_omits_empty_sections() -> None:
    cs = ChangeSet(changes=(change(ChangeKind.UPDATED, "Sodium", "0.5", "0.6"),))
    text = render_changelog(
        cs, version="v1", title_format="{version}", now=datetime(2026, 7, 9)
    )
    assert "## Added" not in text
    assert "## Removed" not in text
    assert "**Minecraft:**" not in text
