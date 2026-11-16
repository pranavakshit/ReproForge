"""Streaming multi-algorithm artifact hashing and byte equality verification."""

import hashlib
from pathlib import Path
from typing import Dict, Tuple


class ArtifactHasher:
    """Computes streaming cryptographic hashes and performs chunked byte comparisons."""

    DEFAULT_CHUNK_SIZE = 65536  # 64 KB

    @classmethod
    def compute_sha256(cls, file_path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
        """Compute SHA-256 digest of a file in streaming chunks."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as fp:
            while chunk := fp.read(chunk_size):
                hasher.update(chunk)
        return hasher.hexdigest()

    @classmethod
    def compute_blake2b(cls, file_path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
        """Compute BLAKE2b digest of a file in streaming chunks."""
        hasher = hashlib.blake2b()
        with open(file_path, "rb") as fp:
            while chunk := fp.read(chunk_size):
                hasher.update(chunk)
        return hasher.hexdigest()

    @classmethod
    def compute_hashes(cls, file_path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Dict[str, str]:
        """Compute both SHA-256 and BLAKE2b in a single streaming pass."""
        sha_hasher = hashlib.sha256()
        blake_hasher = hashlib.blake2b()

        with open(file_path, "rb") as fp:
            while chunk := fp.read(chunk_size):
                sha_hasher.update(chunk)
                blake_hasher.update(chunk)

        return {
            "sha256": sha_hasher.hexdigest(),
            "blake2b": blake_hasher.hexdigest(),
        }

    @classmethod
    def are_files_identical(cls, path1: Path, path2: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Tuple[bool, int]:
        """Check if two files are byte-for-byte identical without loading entire files in memory.

        Returns (is_identical, first_divergent_byte_offset). If identical, offset is -1.
        """
        stat1 = path1.stat()
        stat2 = path2.stat()

        if stat1.st_size != stat2.st_size:
            return False, 0

        offset = 0
        with open(path1, "rb") as fp1, open(path2, "rb") as fp2:
            while True:
                b1 = fp1.read(chunk_size)
                b2 = fp2.read(chunk_size)

                if b1 != b2:
                    for i in range(len(b1)):
                        if i >= len(b2) or b1[i] != b2[i]:
                            return False, offset + i
                    return False, offset

                if not b1:
                    break

                offset += len(b1)

        return True, -1
