# SPDX-License-Identifier: Apache-2.0
"""Save/restore a local production workspace with deduplicated, hashed bytes."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(root, archive):
    files, blobs = {}, {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        data = path.read_bytes()
        key = digest(data)
        files[path.relative_to(root).as_posix()] = key
        blobs[key] = data
    manifest = {"format": "sprite-gen-local-workspace-v1", "files": files}
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip:
        zip.writestr("manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
        for key, data in sorted(blobs.items()):
            zip.writestr("blobs/" + key, data)
    return {
        "files": len(files),
        "unique_blobs": len(blobs),
        "bytes": archive.stat().st_size,
        "sha256": digest(archive.read_bytes()),
    }


def restore(archive, root):
    if root.exists():
        raise ValueError("restore into a new directory; existing art is preserved")
    payloads = {}
    with zipfile.ZipFile(archive) as zip:
        manifest = json.loads(zip.read("manifest.json"))
        if manifest.get("format") != "sprite-gen-local-workspace-v1":
            raise ValueError("unknown workspace archive")
        for name, key in manifest["files"].items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                raise ValueError("unsafe archive path")
            data = zip.read("blobs/" + key)
            if digest(data) != key:
                raise ValueError("changed archive blob")
            payloads[name] = data
    for name, data in payloads.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {"files_restored": len(payloads)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["save", "restore"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            globals()[args.mode](args.root, args.archive)
            if args.mode == "save"
            else restore(args.archive, args.root)
        )
    )
