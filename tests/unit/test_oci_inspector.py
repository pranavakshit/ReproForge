"""Unit tests for OCIImageInspector."""

import io
import json
import tarfile
from pathlib import Path
from backend.app.comparison.oci import OCIImageInspector


def test_inspect_synthetic_container_image(tmp_path: Path):
    """Verify inspection of OCI container image archive with manifest, config, and layers."""
    image_tar = tmp_path / "synthetic_image.tar"

    # 1. Create a dummy layer tar
    layer_tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=layer_tar_bytes, mode="w") as ltf:
        file_data = b"echo 'hello from container layer'"
        info = tarfile.TarInfo(name="bin/app")
        info.size = len(file_data)
        info.mtime = 1700000000
        info.mode = 0o755
        ltf.addfile(info, io.BytesIO(file_data))
    layer_bytes = layer_tar_bytes.getvalue()

    # 2. Config JSON
    config_dict = {
        "architecture": "amd64",
        "os": "linux",
        "created": "2024-01-01T12:00:00Z",
        "config": {"Env": ["PATH=/usr/local/bin:/usr/bin", "APP_ENV=production"]},
        "rootfs": {"type": "layers", "diff_ids": ["sha256:1111111111111111111111111111111111111111111111111111111111111111"]},
    }
    config_bytes = json.dumps(config_dict).encode("utf-8")
    config_filename = "abc123config.json"
    layer_filename = "layer1/layer.tar"

    # 3. Manifest JSON
    manifest = [
        {
            "Config": config_filename,
            "RepoTags": ["synthetic-app:v1"],
            "Layers": [layer_filename],
        }
    ]
    manifest_bytes = json.dumps(manifest).encode("utf-8")

    # 4. Pack into outer image.tar
    with tarfile.open(image_tar, "w") as tf:
        # manifest.json
        m_info = tarfile.TarInfo(name="manifest.json")
        m_info.size = len(manifest_bytes)
        tf.addfile(m_info, io.BytesIO(manifest_bytes))

        # config.json
        c_info = tarfile.TarInfo(name=config_filename)
        c_info.size = len(config_bytes)
        tf.addfile(c_info, io.BytesIO(config_bytes))

        # layer.tar
        l_info = tarfile.TarInfo(name=layer_filename)
        l_info.size = len(layer_bytes)
        tf.addfile(l_info, io.BytesIO(layer_bytes))

    # Inspect image tar
    work_dir = tmp_path / "oci_work"
    details = OCIImageInspector.inspect(image_tar, work_dir, unpack_layers=True)

    assert details.architecture == "amd64"
    assert details.os_name == "linux"
    assert details.created_timestamp == "2024-01-01T12:00:00Z"
    assert "APP_ENV=production" in details.environment_variables
    assert details.total_layers == 1

    layer0 = details.layers[0]
    assert layer0.layer_tar_path == layer_filename
    assert layer0.tree is not None
    assert layer0.tree.get_entry("bin/app") is not None
    assert layer0.tree.get_entry("bin/app").size_bytes == len(b"echo 'hello from container layer'")
