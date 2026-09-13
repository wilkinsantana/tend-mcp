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


def saved_image(path, *, unsafe=False, wrong_digest=False):
    layer = b"inert layer bytes; fixture is not an executable image"
    digest = "sha256:" + hashlib.sha256(layer).hexdigest()
    config = {
        "os": "linux",
        "architecture": "amd64",
        "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "a" * 64 if wrong_digest else digest]},
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


@pytest.mark.parametrize("reason,code", normalizer.ERROR_EXIT_CODES.items())
def test_cli_classifies_only_fixed_rejection_codes(monkeypatch, capsys, reason, code):
    import sys

    monkeypatch.setattr(sys, "argv", ["normalize", "--source", "unused", "--layout", "unused", "--load-archive", "unused"])

    def fail(*args):
        raise ValueError(reason)

    monkeypatch.setattr(normalizer, "normalize", fail)
    with pytest.raises(SystemExit) as caught:
        normalizer.main()
    assert caught.value.code == code
    assert capsys.readouterr().err == "Worker image normalization failed; discard this disposable build workspace.\n"


@pytest.mark.parametrize(
    "error",
    [ValueError("private metadata"), OSError("private path"), KeyError("private field"), AttributeError("private detail")],
)
def test_cli_does_not_echo_unknown_failures(monkeypatch, capsys, error):
    import sys

    monkeypatch.setattr(sys, "argv", ["normalize", "--source", "unused", "--layout", "unused", "--load-archive", "unused"])

    def fail(*args):
        raise error

    monkeypatch.setattr(normalizer, "normalize", fail)
    with pytest.raises(SystemExit) as caught:
        normalizer.main()
    assert caught.value.code == 2
    assert "private" not in capsys.readouterr().err


def test_real_cli_digest_rejection_is_distinguishable(tmp_path):
    import subprocess
    import sys

    source = tmp_path / "saved.tar"
    saved_image(source, wrong_digest=True)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--layout",
            str(tmp_path / "oci"),
            "--load-archive",
            str(tmp_path / "load.tar"),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == normalizer.ERROR_EXIT_CODES["build_layer_digest_mismatch"]
    assert not result.stdout and str(tmp_path).encode() not in result.stderr
