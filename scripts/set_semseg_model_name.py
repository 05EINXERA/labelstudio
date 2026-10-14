"""Record the training-run name inside a hull-model checkpoint.

The app shows this name on the Hull button (ml/semseg.py semseg_model_info).
train_semseg.py stores it from 2026-10-14 on; older checkpoints lack it, and
the installed copy is always called hull_semseg.pt, so its file name says
nothing about which run it is. Run from the repo root:

    python scripts/set_semseg_model_name.py models/detector/hull_semseg_v5_own.pt hull_v5_own

Only the name is added; the weights are unchanged. The file is rewritten
through a temporary file and a rename, and keeps its modification time.
"""
import argparse
import os
import sys
from pathlib import Path

import torch


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("checkpoint", help="a .pt file written by train_semseg.py")
    ap.add_argument("name", help="training-run name, e.g. hull_v5_own")
    args = ap.parse_args()

    path = Path(args.checkpoint)
    state = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(state, dict) or "model" not in state or "classes" not in state:
        sys.exit(f"Error: {path} is not a train_semseg.py checkpoint")
    previous = state.get("name")
    state["name"] = args.name
    stat = path.stat()
    tmp = path.with_name(path.name + ".tmp")
    torch.save(state, tmp)
    os.utime(tmp, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.replace(tmp, path)
    print(f"{path}: name {previous!r} -> {args.name!r} (epoch {state.get('epoch')})")


if __name__ == "__main__":
    main()
