"""OCI and Docker container image structure inspection and layer differential analysis."""

import json
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.app.comparison.hasher import ArtifactHasher
from backend.app.comparison.unpacker import ArchiveUnpacker, ArtifactTree
from backend.app.core.logging import get_logger

logger = get_logger("reproforge.comparison.oci")


class OCILayerInfo(BaseModel):
    """Metadata for an individual container image filesystem layer."""

    layer_index: int
    layer_tar_path: str
    layer_tar_sha256: str
    diff_id: Optional[str] = None
    size_bytes: int = 0
    tree: Optional[ArtifactTree] = None


class OCIImageDetails(BaseModel):
    """Extracted structure, configuration, and layer catalog of a container image tarball."""

    image_tar_path: str
    manifest: List[Dict[str, Any]]
    config_filename: str
    config_json: Dict[str, Any]
    created_timestamp: Optional[str] = None
    environment_variables: List[str] = Field(default_factory=list)
    architecture: str = ""
    os_name: str = ""
    layers: List[OCILayerInfo] = Field(default_factory=list)
    total_layers: int = 0
    aggregated_content_digest: str = ""


class OCIImageInspector:
    """Inspects Docker/OCI image tarballs (docker save) and extracts layer hierarchies."""

    @classmethod
    def inspect(cls, image_tar_path: Path, work_dir: Path, unpack_layers: bool = True) -> OCIImageDetails:
        """Inspect and optionally unpack container image tarball."""
        extract_dir = work_dir / f"oci_{image_tar_path.stem}"
        extract_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Inspecting container image archive: {image_tar_path}")

        # Extract manifest and config JSON
        with tarfile.open(image_tar_path, "r:*") as tf:
            manifest_member = tf.getmember("manifest.json")
            manifest_bytes = tf.extractfile(manifest_member).read()
            manifest = json.loads(manifest_bytes.decode("utf-8"))

            primary_entry = manifest[0]
            config_file = primary_entry.get("Config", "")
            layer_files = primary_entry.get("Layers", [])

            config_member = tf.getmember(config_file)
            config_bytes = tf.extractfile(config_member).read()
            config_json = json.loads(config_bytes.decode("utf-8"))

            # Extract config file to disk
            (extract_dir / config_file).parent.mkdir(parents=True, exist_ok=True)
            (extract_dir / config_file).write_bytes(config_bytes)

        # Parse config fields
        created = config_json.get("created")
        env_vars = config_json.get("config", {}).get("Env", [])
        arch = config_json.get("architecture", "")
        os_name = config_json.get("os", "")
        diff_ids = config_json.get("rootfs", {}).get("diff_ids", [])

        # Process layers
        layers: List[OCILayerInfo] = []
        with tarfile.open(image_tar_path, "r:*") as tf:
            for idx, layer_rel in enumerate(layer_files):
                layer_member = tf.getmember(layer_rel)
                layer_bytes = tf.extractfile(layer_member).read()
                layer_dest = extract_dir / layer_rel
                layer_dest.parent.mkdir(parents=True, exist_ok=True)
                layer_dest.write_bytes(layer_bytes)

                layer_sha = ArtifactHasher.compute_sha256(layer_dest)
                diff_id = diff_ids[idx] if idx < len(diff_ids) else None

                tree = None
                if unpack_layers:
                    layer_unpack_dir = extract_dir / f"layer_{idx}_rootfs"
                    tree = ArchiveUnpacker.unpack_tar(layer_dest, layer_unpack_dir)

                layers.append(
                    OCILayerInfo(
                        layer_index=idx,
                        layer_tar_path=layer_rel,
                        layer_tar_sha256=layer_sha,
                        diff_id=diff_id,
                        size_bytes=layer_member.size,
                        tree=tree,
                    )
                )

        import hashlib
        agg_hasher = hashlib.sha256()
        for lyr in layers:
            if lyr.tree:
                agg_hasher.update(lyr.tree.content_only_digest.encode("utf-8"))
            else:
                agg_hasher.update(lyr.layer_tar_sha256.encode("utf-8"))
        aggregated_content = agg_hasher.hexdigest()

        return OCIImageDetails(
            image_tar_path=str(image_tar_path),
            manifest=manifest,
            config_filename=config_file,
            config_json=config_json,
            created_timestamp=created,
            environment_variables=env_vars,
            architecture=arch,
            os_name=os_name,
            layers=layers,
            total_layers=len(layers),
            aggregated_content_digest=aggregated_content,
        )
