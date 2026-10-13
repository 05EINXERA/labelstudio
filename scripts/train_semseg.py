"""Train a hull-condition semantic-segmentation model.

Reads a dataset from build_semseg_dataset.py (full-size photos + class-id
masks). Trains on random crops taken at random zoom, validates on whole
photos at `--eval-scale`, and reports IoU per class. Run from the repo root:

    python scripts/train_semseg.py --data C:\\datasets\\semseg_v2 --name hull_v2

Outputs go to <--out>/<name>/: best.pt, last.pt, results.csv, val_pred.jpg,
and resume.pt (training state; `--resume` continues an interrupted run).
Copy best.pt to models/detector/hull_semseg.pt to use it in the app.
"""
import argparse
import csv
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torch.optim.swa_utils import AveragedModel
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ml.semseg_arch import STRIDE, build_semseg_net  # noqa: E402

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
# Masks are scanned at this stride when looking for rare-class pixels.
RARE_STRIDE = 8


def to_tensor(img: np.ndarray) -> torch.Tensor:
    return torch.from_numpy(((img.astype(np.float32) / 255.0 - MEAN) / STD).transpose(2, 0, 1))


class SegData(Dataset):
    """Train: random crop at a random zoom. Eval: the whole photo at `scale`."""

    def __init__(self, root: Path, split: str, crop: int = 0, repeats: int = 1,
                 scale: float = 1.0, zoom: tuple[float, float] = (0.5, 1.0),
                 rare: tuple[int, ...] = (), rare_prob: float = 0.0):
        self.images = sorted((root / "images" / split).glob("*.jpg"))
        self.masks = [root / "masks" / split / (p.stem + ".png") for p in self.images]
        self.crop, self.repeats, self.scale, self.zoom = crop, repeats, scale, zoom
        self.rare, self.rare_prob = tuple(rare), rare_prob
        # Which rare classes each photo contains, so crops can be aimed at them.
        self.rare_present = []
        if self.rare:
            for p in self.masks:
                found = np.unique(np.asarray(Image.open(p))[::RARE_STRIDE, ::RARE_STRIDE])
                self.rare_present.append([c for c in self.rare if c in found])

    def sample_weights(self, boost: float) -> list[float]:
        """Per-item draw weights: photos holding a rare class are drawn `boost` times as often."""
        per_photo = [boost if present else 1.0 for present in self.rare_present] or [1.0] * len(self.images)
        return per_photo * self.repeats

    def __len__(self) -> int:
        return len(self.images) * self.repeats

    def __getitem__(self, index: int):
        i = index % len(self.images)
        img, mask = Image.open(self.images[i]).convert("RGB"), Image.open(self.masks[i])
        if not self.crop:
            size = (round(img.width * self.scale), round(img.height * self.scale))
            a = np.asarray(img.resize(size, Image.BILINEAR))
            m = np.asarray(mask.resize(size, Image.NEAREST)).astype(np.int64)
            return to_tensor(a), torch.from_numpy(m)
        # Cut a window of crop/zoom source pixels, then resize it to the crop.
        zoom = random.uniform(*self.zoom)
        side = min(round(self.crop / zoom), img.width, img.height)
        x, y = random.randint(0, img.width - side), random.randint(0, img.height - side)
        if self.rare_present and self.rare_present[i] and random.random() < self.rare_prob:
            # Aim the window at a random pixel of one of the rare classes in this photo.
            small = np.asarray(mask)[::RARE_STRIDE, ::RARE_STRIDE]
            ys, xs = np.nonzero(small == random.choice(self.rare_present[i]))
            if len(xs):
                k = random.randrange(len(xs))
                cx, cy = int(xs[k]) * RARE_STRIDE, int(ys[k]) * RARE_STRIDE
                x = min(max(cx - random.randint(side // 4, 3 * side // 4), 0), img.width - side)
                y = min(max(cy - random.randint(side // 4, 3 * side // 4), 0), img.height - side)
        box = (x, y, x + side, y + side)
        out = (self.crop, self.crop)
        a = np.asarray(img.crop(box).resize(out, Image.BILINEAR)).astype(np.float32)
        m = np.asarray(mask.crop(box).resize(out, Image.NEAREST)).astype(np.int64)
        if random.random() < 0.5:
            a, m = a[:, ::-1], m[:, ::-1]
        if random.random() < 0.5:
            a, m = a[::-1], m[::-1]
        a = np.clip((a - 127.5) * random.uniform(0.8, 1.2) + 127.5 + random.uniform(-20, 20), 0, 255)
        a = np.clip(a * np.random.uniform(0.92, 1.08, 3).astype(np.float32), 0, 255)  # white-balance shift
        return to_tensor(np.ascontiguousarray(a)), torch.from_numpy(np.ascontiguousarray(m))


def warmup_ema(decay: float):
    """EMA update whose decay ramps up from 0.1, so the average tracks the
    model during the first epochs instead of clinging to the initial weights."""

    @torch.no_grad()
    def update(averaged, current, num_averaged):
        d = min(decay, (1 + int(num_averaged)) / (10 + int(num_averaged)))
        for avg, cur in zip(averaged, current):
            if avg.is_floating_point():
                avg.lerp_(cur, 1 - d)
            else:  # integer buffers such as BatchNorm's step counter
                avg.copy_(cur)

    return update


def class_weights(data: SegData, n: int, power: float) -> torch.Tensor:
    counts = np.zeros(n, np.int64)
    for p in data.masks[::3]:
        counts += np.bincount(np.asarray(Image.open(p)).ravel()[::16], minlength=n)[:n]
    freq = counts / counts.sum()
    w = 1.0 / np.power(np.maximum(freq, 1e-4), power)
    return torch.tensor(w / w.mean(), dtype=torch.float32)


def dice_loss(logits: torch.Tensor, target: torch.Tensor, n: int) -> torch.Tensor:
    prob = logits.float().softmax(1)
    onehot = F.one_hot(target, n).permute(0, 3, 1, 2).float()
    inter = (prob * onehot).sum((0, 2, 3))
    denom = prob.sum((0, 2, 3)) + onehot.sum((0, 2, 3))
    present = onehot.sum((0, 2, 3)) > 0
    return 1 - ((2 * inter + 1) / (denom + 1))[present].mean()


def predict(model, img: torch.Tensor) -> torch.Tensor:
    """Class map for a batch of whole images, padded up to the network stride."""
    h, w = img.shape[-2:]
    pad = ((-w) % STRIDE, (-h) % STRIDE)
    if pad != (0, 0):
        img = F.pad(img, (0, pad[0], 0, pad[1]), mode="reflect")
    return model(img)["out"][..., :h, :w].argmax(1)


@torch.no_grad()
def evaluate(model, loader, n: int, device) -> np.ndarray:
    model.eval()
    conf = torch.zeros(n, n, dtype=torch.int64, device=device)
    for img, mask in loader:
        img, mask = img.to(device), mask.to(device)
        with torch.autocast(device.type, enabled=device.type == "cuda"):
            pred = predict(model, img)
        conf += torch.bincount(mask.view(-1) * n + pred.view(-1), minlength=n * n).view(n, n)
    return conf.cpu().numpy()


def iou_from(conf: np.ndarray) -> np.ndarray:
    tp = np.diag(conf).astype(np.float64)
    union = conf.sum(0) + conf.sum(1) - tp
    return np.where(union > 0, tp / np.maximum(union, 1), np.nan)


def defect_stats(conf: np.ndarray) -> tuple[float, float, float]:
    """Any-defect vs background: (IoU, precision, recall)."""
    conf = conf.astype(np.float64)
    tp, fn, fp = conf[1:, 1:].sum(), conf[1:, 0].sum(), conf[0, 1:].sum()
    return tp / max(tp + fn + fp, 1), tp / max(tp + fp, 1), tp / max(tp + fn, 1)


@torch.no_grad()
def save_predictions(model, data: SegData, classes: list, device, out: Path, count: int = 6) -> None:
    model.eval()
    palette = np.array([c["rgb"] for c in classes], np.uint8)
    rows = []
    for i in np.linspace(0, len(data.images) - 1, min(count, len(data.images))).astype(int):
        tensor, gt = data[i]
        with torch.autocast(device.type, enabled=device.type == "cuda"):
            pred = predict(model, tensor[None].to(device))[0].cpu().numpy()
        img = np.clip((tensor.numpy().transpose(1, 2, 0) * STD + MEAN) * 255, 0, 255).astype(np.uint8)
        tiles = [img, (0.4 * img + 0.6 * palette[gt.numpy()]).astype(np.uint8), (0.4 * img + 0.6 * palette[pred]).astype(np.uint8)]
        rows.append(np.concatenate([np.asarray(Image.fromarray(t).resize((432, 324))) for t in tiles], 1))
    Image.fromarray(np.concatenate(rows, 0)).save(out, quality=85)  # columns: photo | label | prediction


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--name", default="hull_semseg")
    ap.add_argument("--out", default=str(REPO / "runs" / "semseg"), help="parent folder for the run")
    ap.add_argument("--arch", default="unet_resnet34", choices=["unet_resnet34", "deeplabv3_mobilenet_v3_large"])
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--patience", type=int, default=30,
                    help="stop after this many epochs without a new best score; 0 never stops early")
    ap.add_argument("--batch", type=int, default=6)
    ap.add_argument("--crop", type=int, default=512)
    ap.add_argument("--repeats", type=int, default=3, help="random crops per photo per epoch")
    ap.add_argument("--zoom", type=float, nargs=2, default=(0.5, 1.0), help="random zoom range for training crops")
    ap.add_argument("--eval-scale", type=float, default=0.75, help="photo scale for validation and for the app")
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--weight-power", type=float, default=0.25,
                    help="class weight = frequency^-power; 0 disables weighting, 0.5 over-flags rare classes")
    ap.add_argument("--rare-classes", default="dirt2,exposed_steel_plate,marine_organisms,rust",
                    help="comma-separated class names to over-sample; empty string disables")
    ap.add_argument("--rare-prob", type=float, default=0.5, help="share of crops aimed at a rare-class pixel")
    ap.add_argument("--rare-boost", type=float, default=2.0,
                    help="how much more often photos with a rare class are drawn")
    ap.add_argument("--ema", type=float, default=0.999,
                    help="decay of the averaged weights that are validated and saved; 0 uses the raw weights")
    ap.add_argument("--init", help="checkpoint to start from (fine-tune), e.g. an earlier best.pt")
    ap.add_argument("--resume", action="store_true", help="continue the run in <out>/<name> from resume.pt")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--no-pretrained", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    root, device = Path(args.data), torch.device(args.device)
    classes = json.loads((root / "classes.json").read_text(encoding="utf-8"))
    n = len(classes)
    out = Path(args.out) / args.name
    out.mkdir(parents=True, exist_ok=True)

    names = [c["name"] for c in classes]
    wanted = [x.strip() for x in args.rare_classes.split(",") if x.strip()]
    unknown = [x for x in wanted if x not in names]
    if unknown:
        sys.exit(f"Error: --rare-classes not in classes.json: {unknown}")
    rare = tuple(names.index(x) for x in wanted)

    train = SegData(root, "train", crop=args.crop, repeats=args.repeats, zoom=tuple(args.zoom),
                    rare=rare, rare_prob=args.rare_prob)
    val = SegData(root, "val", scale=args.eval_scale)
    if not train.images or not val.images:
        sys.exit(f"Error: no images under {root}")
    sampler = None
    if rare and args.rare_boost != 1.0:
        sampler = WeightedRandomSampler(train.sample_weights(args.rare_boost), num_samples=len(train),
                                        replacement=True)
    train_loader = DataLoader(train, batch_size=args.batch, shuffle=sampler is None, sampler=sampler,
                              num_workers=args.workers, drop_last=True, pin_memory=True,
                              persistent_workers=args.workers > 0)
    val_loader = DataLoader(val, batch_size=1, num_workers=min(2, args.workers))

    try:
        model = build_semseg_net(args.arch, n, pretrained=not args.no_pretrained)
    except OSError as exc:  # no network for the one-off ImageNet weight download
        print(f"WARNING: could not load pretrained encoder ({exc}); training from scratch")
        model = build_semseg_net(args.arch, n, pretrained=False)
    if args.init:
        model.load_state_dict(torch.load(args.init, map_location="cpu", weights_only=True)["model"])
        print(f"initialised from {args.init}")
    model = model.to(device)
    # The averaged copy is what gets validated and saved: it is steadier from
    # one epoch to the next than the raw weights.
    ema = None
    if args.ema > 0:
        ema = AveragedModel(model, multi_avg_fn=warmup_ema(args.ema), use_buffers=True)
    weights = class_weights(train, n, args.weight_power).to(device)
    optim = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(optim, max_lr=args.lr, total_steps=args.epochs * len(train_loader),
                                                pct_start=0.1)
    scaler = torch.amp.GradScaler(device.type, enabled=device.type == "cuda")
    with_rare = sum(1 for present in train.rare_present if present)
    print(f"{args.arch} on {device} | train {len(train.images)} photos ({with_rare} with a rare class), "
          f"val {len(val.images)} | {len(train_loader)} it/epoch | "
          f"class weights {[round(float(w), 2) for w in weights]}", flush=True)

    best, best_epoch, first_epoch = -1.0, 0, 1
    resume_path = out / "resume.pt"
    if args.resume:
        if not resume_path.is_file():
            sys.exit(f"Error: nothing to resume, {resume_path} not found")
        state = torch.load(resume_path, map_location=device, weights_only=True)
        model.load_state_dict(state["raw"])
        optim.load_state_dict(state["optim"])
        sched.load_state_dict(state["sched"])
        scaler.load_state_dict(state["scaler"])
        if ema is not None and state.get("ema"):
            ema.load_state_dict(state["ema"])
        best, best_epoch, first_epoch = state["best"], state["best_epoch"], state["epoch"] + 1
        print(f"resumed after epoch {state['epoch']} (best {best:.3f} at epoch {best_epoch})", flush=True)

    with (out / "results.csv").open("a" if args.resume else "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not args.resume:
            writer.writerow(["epoch", "train_loss", "miou", "miou_defects", "defect_iou", "defect_precision",
                             "defect_recall"] + [f"iou_{x}" for x in names] + ["gpu_gb", "sec"])
        for epoch in range(first_epoch, args.epochs + 1):
            model.train()
            start, running = time.time(), 0.0
            if device.type == "cuda":
                torch.cuda.reset_peak_memory_stats()
            for img, mask in train_loader:
                img, mask = img.to(device, non_blocking=True), mask.to(device, non_blocking=True)
                with torch.autocast(device.type, enabled=device.type == "cuda"):
                    logits = model(img)["out"]
                    loss = F.cross_entropy(logits, mask, weight=weights) + dice_loss(logits, mask, n)
                optim.zero_grad(set_to_none=True)
                scaler.scale(loss).backward()
                scaler.step(optim)
                scaler.update()
                sched.step()
                if ema is not None:
                    ema.update_parameters(model)
                running += loss.item()
            scored = ema.module if ema is not None else model
            conf = evaluate(scored, val_loader, n, device)
            iou = iou_from(conf)
            miou, defects = float(np.nanmean(iou)), float(np.nanmean(iou[1:]))
            d_iou, d_prec, d_rec = defect_stats(conf)
            gpu = torch.cuda.max_memory_allocated() / 2**30 if device.type == "cuda" else 0.0
            writer.writerow([epoch, round(running / len(train_loader), 4), round(miou, 4), round(defects, 4),
                             round(d_iou, 4), round(d_prec, 4), round(d_rec, 4)]
                            + [round(float(v), 4) for v in iou] + [round(gpu, 2), round(time.time() - start)])
            fh.flush()
            state = {"model": scored.state_dict(), "classes": classes, "arch": args.arch,
                     "scale": args.eval_scale, "epoch": epoch, "miou_defects": defects}
            torch.save(state, out / "last.pt")
            if defects > best:
                best, best_epoch = defects, epoch
                torch.save(state, out / "best.pt")
            torch.save({"raw": model.state_dict(), "optim": optim.state_dict(), "sched": sched.state_dict(),
                        "scaler": scaler.state_dict(), "ema": ema.state_dict() if ema is not None else None,
                        "epoch": epoch, "best": best, "best_epoch": best_epoch}, resume_path)
            print(f"epoch {epoch}/{args.epochs} loss {running / len(train_loader):.3f} defect-mIoU {defects:.3f}"
                  f"{' *' if defects == best else ''} | any-defect IoU {d_iou:.3f} P {d_prec:.2f} R {d_rec:.2f} | "
                  f"gpu {gpu:.2f}G | {time.time() - start:.0f}s", flush=True)
            if args.patience and epoch - best_epoch >= args.patience:
                print(f"no new best for {args.patience} epochs; stopping at epoch {epoch}", flush=True)
                break

    model.load_state_dict(torch.load(out / "best.pt", map_location=device, weights_only=True)["model"])
    conf = evaluate(model, val_loader, n, device)
    iou = iou_from(conf)
    recall = np.diag(conf) / np.maximum(conf.sum(1), 1)
    print("\nBest checkpoint on validation (IoU, share of labelled pixels found):")
    for name, value, found in zip(names, iou, recall):
        print(f"  {name:26s} {value:.3f}  {found * 100:5.1f}%")
    d_iou, d_prec, d_rec = defect_stats(conf)
    print(f"  defect-mIoU (classes 1-{n - 1}): {np.nanmean(iou[1:]):.3f}")
    print(f"  any-defect vs sound: IoU {d_iou:.3f} precision {d_prec:.3f} recall {d_rec:.3f}")
    save_predictions(model, val, classes, device, out / "val_pred.jpg")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
