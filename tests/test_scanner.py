import zipfile
from pathlib import Path

from tests.conftest import make_fabric_jar, make_neoforge_jar

from modbridge.mods.scanner import scan_mods_dir


def test_scan_fabric_jar(tmp_path: Path) -> None:
    make_fabric_jar(tmp_path / "mods" / "sodium.jar", "sodium", "0.6.0", "Sodium")
    manifest = scan_mods_dir(tmp_path / "mods")
    assert len(manifest.mods) == 1
    m = manifest.mods[0]
    assert (m.mod_id, m.name, m.version) == ("sodium", "Sodium", "0.6.0")


def test_scan_neoforge_jar(tmp_path: Path) -> None:
    make_neoforge_jar(tmp_path / "mods" / "lithium.jar", "lithium", "1.2.3")
    manifest = scan_mods_dir(tmp_path / "mods")
    assert manifest.mods[0].mod_id == "lithium"
    assert manifest.mods[0].version == "1.2.3"


def test_jar_version_placeholder_falls_back_to_manifest(tmp_path: Path) -> None:
    jar = tmp_path / "mods" / "thing.jar"
    jar.parent.mkdir(parents=True)
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(
            "META-INF/neoforge.mods.toml",
            '[[mods]]\nmodId = "thing"\nversion = "${file.jarVersion}"\n',
        )
        zf.writestr(
            "META-INF/MANIFEST.MF",
            "Manifest-Version: 1.0\nImplementation-Version: 9.9.9\n",
        )
    manifest = scan_mods_dir(tmp_path / "mods")
    assert manifest.mods[0].version == "9.9.9"


def test_unreadable_jar_still_tracked(tmp_path: Path) -> None:
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "corrupt.jar").write_bytes(b"not a zip at all")
    manifest = scan_mods_dir(mods)
    assert len(manifest.mods) == 1
    assert manifest.mods[0].mod_id is None
    assert manifest.mods[0].sha256


def test_missing_dir_is_empty_manifest(tmp_path: Path) -> None:
    assert scan_mods_dir(tmp_path / "nope").mods == ()
