from modbridge.domain.models import ChangeKind, ModInfo, ModsManifest, diff_manifests


def mod(filename: str, sha: str, mod_id: str | None = None, version: str | None = None) -> ModInfo:
    return ModInfo(filename=filename, sha256=sha, mod_id=mod_id, version=version)


def test_diff_update_by_mod_id_survives_rename() -> None:
    old = ModsManifest((mod("sodium-0.5.jar", "aaa", "sodium", "0.5"),))
    new = ModsManifest((mod("sodium-0.6.jar", "bbb", "sodium", "0.6"),))
    cs = diff_manifests(old, new)
    assert len(cs.changes) == 1
    change = cs.changes[0]
    assert change.kind == ChangeKind.UPDATED
    assert change.old_version == "0.5"
    assert change.new_version == "0.6"


def test_diff_added_and_removed() -> None:
    old = ModsManifest((mod("a.jar", "aaa", "a"),))
    new = ModsManifest((mod("b.jar", "bbb", "b"),))
    cs = diff_manifests(old, new)
    assert {c.kind for c in cs.changes} == {ChangeKind.ADDED, ChangeKind.REMOVED}


def test_diff_by_filename_when_no_metadata() -> None:
    old = ModsManifest((mod("mystery.jar", "aaa"),))
    new = ModsManifest((mod("mystery.jar", "bbb"),))
    cs = diff_manifests(old, new)
    assert len(cs.changes) == 1
    assert cs.changes[0].kind == ChangeKind.UPDATED


def test_diff_no_changes() -> None:
    m = ModsManifest((mod("a.jar", "aaa", "a"),))
    assert diff_manifests(m, m).is_empty


def test_content_hash_order_independent() -> None:
    a = mod("a.jar", "aaa", "a")
    b = mod("b.jar", "bbb", "b")
    assert ModsManifest((a, b)).content_hash() == ModsManifest((b, a)).content_hash()


def test_manifest_roundtrip() -> None:
    m = ModsManifest((mod("a.jar", "aaa", "a", "1.0"),))
    assert ModsManifest.from_dict(m.to_dict()) == m


def test_summary() -> None:
    cs = diff_manifests(
        ModsManifest((mod("a.jar", "aaa", "a"),)),
        ModsManifest((mod("a.jar", "bbb", "a"), mod("b.jar", "ccc", "b"))),
    )
    assert "1 updated" in cs.summary()
    assert "1 added" in cs.summary()
