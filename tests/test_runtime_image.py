import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "normalize-runtime-image.py"
spec = importlib.util.spec_from_file_location("image_normalizer", SCRIPT)
assert spec and spec.loader
normalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalizer)


def saved_image(path, *, unsafe=False):
    layer = b"inert layer bytes; fixture is not an executable image"
    digest = "sha256:" + hashlib.sha256(layer).hexdigest()
    config = {
        "os": "linux",
        "architecture": "amd64",
        "rootfs": {"type": "layers", "diff_ids": [digest]},
        "config": {
            "User": "0" if unsafe else "65532:65532",
            "WorkingDir": "/app",
            "Entrypoint": normalizer.ENTRYPOINT,
            "Env": ["PUBLIC_BUILD_METADATA=discard"],
            "Cmd": [],
        },
        "history": [{"created_by": "trusted build metadata"}],
    }
    files = {
        "config.json": json.dumps(config).encode(),
        "layer.tar": layer,
        "manifest.json": json.dumps(
            [{"Config": "config.json", "RepoTags": ["fixture:only"], "Layers": ["layer.tar"]}]
        ).encode(),
    }
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))


def test_normalized_layout_and_load_archive_share_exact_tag_free_config(tmp_path):
    source, layout, load = tmp_path / "saved.tar", tmp_path / "oci", tmp_path / "load.tar"
    saved_image(source)
    image_id = normalizer.normalize(source, layout, load)
    config = (layout / "blobs" / "sha256" / image_id.removeprefix("sha256:")).read_bytes()
    assert image_id == "sha256:" + hashlib.sha256(config).hexdigest()
    assert set(json.loads(config)) == {"os", "architecture", "rootfs", "config"}
    assert b"PUBLIC_BUILD_METADATA" not in config and b"trusted build metadata" not in config
    with tarfile.open(load, "r:") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        assert manifest[0]["RepoTags"] == []
        assert archive.extractfile(manifest[0]["Config"]).read() == config
    with pytest.raises(ValueError):
        normalizer.normalize(source, layout, load)


def test_wrong_worker_build_cannot_be_normalized(tmp_path):
    source = tmp_path / "saved.tar"
    saved_image(source, unsafe=True)
    with pytest.raises(ValueError, match="wrong_build_entrypoint"):
        normalizer.normalize(source, tmp_path / "oci", tmp_path / "load.tar")
    assert not (tmp_path / "oci").exists()
