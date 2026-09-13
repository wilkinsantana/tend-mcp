import gzip
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


def saved_image(
    path,
    *,
    unsafe=False,
    wrong_digest=False,
    compressed=False,
    layers=(b"inert layer bytes; fixture is not an executable image",),
    corrupt=False,
):
    digests = ["sha256:" + hashlib.sha256(layer).hexdigest() for layer in layers]
    config = {
        "os": "linux",
        "architecture": "amd64",
        "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "a" * 64] if wrong_digest else digests},
        "config": {
            "User": "0" if unsafe else "65532:65532",
            "WorkingDir": "/app",
            "Entrypoint": normalizer.ENTRYPOINT,
            "Env": ["PUBLIC_BUILD_METADATA=discard"],
            "Cmd": [],
        },
        "history": [{"created_by": "trusted build metadata"}],
    }
    files = {}
    names = []
    for number, layer in enumerate(layers):
        body = gzip.compress(layer, mtime=0) if compressed else layer
        if corrupt == "crc":
            body = body[:-8] + bytes([body[-8] ^ 1]) + body[-7:]
        elif corrupt:
            body = body[:-4]
        name = "blobs/sha256/" + hashlib.sha256(body).hexdigest() if compressed else f"layer-{number}.tar"
        names.append(name)
        files[name] = body
    config_bytes = json.dumps(config).encode()
    config_name = "blobs/sha256/" + hashlib.sha256(config_bytes).hexdigest() if compressed else "config.json"
    files[config_name] = config_bytes
    files["manifest.json"] = json.dumps([{"Config": config_name, "RepoTags": ["fixture:only"], "Layers": names}]).encode()
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


def test_gzip_oci_blobs_produce_identical_uncompressed_output(tmp_path):
    identities, archives = [], []
    for compressed in (False, True):
        source, layout, load = (
            tmp_path / f"{compressed}.tar",
            tmp_path / f"{compressed}-oci",
            tmp_path / f"{compressed}-load.tar",
        )
        saved_image(source, compressed=compressed)
        identities.append(normalizer.normalize(source, layout, load))
        archives.append(load.read_bytes())
    assert identities[0] == identities[1]
    assert archives[0] == archives[1]


@pytest.mark.parametrize(
    "wrong_digest,corrupt,layer,reason",
    [
        (True, False, b"inert", "build_layer_digest_mismatch"),
        (False, True, b"inert", "invalid_compressed_build_layer"),
        (False, "crc", b"inert", "invalid_compressed_build_layer"),
        (False, False, b"", "invalid_build_layer"),
    ],
)
def test_compressed_layers_still_require_valid_complete_nonempty_identity(tmp_path, wrong_digest, corrupt, layer, reason):
    source = tmp_path / "saved.tar"
    saved_image(source, compressed=True, wrong_digest=wrong_digest, corrupt=corrupt, layers=(layer,))
    with pytest.raises(ValueError, match=reason):
        normalizer.normalize(source, tmp_path / "oci", tmp_path / "load.tar")
    assert not (tmp_path / "load.tar").exists()


@pytest.mark.parametrize("layers", [(b"x" * 32768,), (b"x" * 10000, b"y" * 10000)])
def test_decompression_is_bounded_across_all_layers(tmp_path, monkeypatch, layers):
    source = tmp_path / "saved.tar"
    saved_image(source, compressed=True, layers=layers)
    monkeypatch.setattr(normalizer, "MAX_BYTES", 16384)
    with pytest.raises(ValueError, match="oversized_normalized_layers"):
        normalizer.normalize(source, tmp_path / "oci", tmp_path / "load.tar")
    assert not (tmp_path / "load.tar").exists()
    assert sum(p.stat().st_size for p in (tmp_path / "oci").rglob("*") if p.is_file()) <= normalizer.MAX_BYTES


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
