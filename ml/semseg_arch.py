"""Model definitions for hull-condition semantic segmentation.

Shared by scripts/train_semseg.py and ml/semseg.py so a checkpoint is always
rebuilt with the code that trained it. A checkpoint names its architecture in
`arch`; `build_semseg_net` maps that name back to a module. Every model
returns `{"out": logits}` at input resolution, like torchvision's.
"""
import torch
import torch.nn.functional as F
from torch import nn
from torchvision.models import MobileNet_V3_Large_Weights, ResNet34_Weights, resnet34
from torchvision.models.segmentation import deeplabv3_mobilenet_v3_large

# Input height/width must be a multiple of this (five 2x downsamplings).
STRIDE = 32


class _Decode(nn.Module):
    """Upsample, concatenate the encoder skip, two 3x3 conv blocks."""

    def __init__(self, in_channels: int, skip_channels: int, out_channels: int):
        super().__init__()
        self.block = nn.Sequential(
            nn.Conv2d(in_channels + skip_channels, out_channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels), nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels), nn.ReLU(inplace=True),
        )

    def forward(self, x, skip):
        x = F.interpolate(x, size=skip.shape[-2:], mode="bilinear", align_corners=False)
        return self.block(torch.cat([x, skip], 1))


class UNetResNet34(nn.Module):
    """U-Net with a ResNet-34 encoder.

    Skip connections down to half resolution keep the thin, ragged patch
    outlines that DeepLabV3 (output stride 16) smears into blobs.
    """

    def __init__(self, num_classes: int, pretrained: bool = False):
        super().__init__()
        encoder = resnet34(weights=ResNet34_Weights.IMAGENET1K_V1 if pretrained else None)
        self.stem = nn.Sequential(encoder.conv1, encoder.bn1, encoder.relu)  # 1/2, 64
        self.pool = encoder.maxpool
        self.layer1, self.layer2 = encoder.layer1, encoder.layer2  # 1/4 64, 1/8 128
        self.layer3, self.layer4 = encoder.layer3, encoder.layer4  # 1/16 256, 1/32 512
        self.up4 = _Decode(512, 256, 256)
        self.up3 = _Decode(256, 128, 128)
        self.up2 = _Decode(128, 64, 64)
        self.up1 = _Decode(64, 64, 64)
        self.head = nn.Conv2d(64, num_classes, 1)

    def forward(self, x):
        s0 = self.stem(x)
        s1 = self.layer1(self.pool(s0))
        s2 = self.layer2(s1)
        s3 = self.layer3(s2)
        y = self.layer4(s3)
        y = self.up1(self.up2(self.up3(self.up4(y, s3), s2), s1), s0)
        logits = F.interpolate(self.head(y), size=x.shape[-2:], mode="bilinear", align_corners=False)
        return {"out": logits}


def build_semseg_net(arch: str, num_classes: int, pretrained: bool = False) -> nn.Module:
    """`pretrained` loads ImageNet encoder weights (training only; needs network once)."""
    if arch == "unet_resnet34":
        return UNetResNet34(num_classes, pretrained)
    if arch == "deeplabv3_mobilenet_v3_large":
        backbone = MobileNet_V3_Large_Weights.IMAGENET1K_V2 if pretrained else None
        return deeplabv3_mobilenet_v3_large(weights=None, weights_backbone=backbone, num_classes=num_classes)
    raise ValueError(f"Unknown semantic segmentation architecture: {arch!r}")


class _SmpWrapper(nn.Module):
    """Wrap a segmentation_models_pytorch model so it returns ``{"out": logits}``.

    smp models return a raw tensor; the rest of the inference pipeline
    (``probabilities_for``) indexes the output with ``["out"]``.
    """

    def __init__(self, model: nn.Module):
        super().__init__()
        self.model = model

    def forward(self, x):
        return {"out": self.model(x)}

    def load_state_dict(self, state_dict, strict: bool = True):
        if any(k.startswith("model.") for k in state_dict.keys()):
            return super().load_state_dict(state_dict, strict=strict)
        return self.model.load_state_dict(state_dict, strict=strict)


def build_smp_net(arch: str, encoder: str, num_classes: int,
                  pretrained: bool = False) -> nn.Module:
    """Build a segmentation_models_pytorch network, wrapped for this app.

    ``arch`` and ``encoder`` come from the checkpoint's
    ``cfg.train.arch`` / ``cfg.train.encoder`` (e.g. ``"unet"`` /
    ``"resnet34"``).
    """
    try:
        import segmentation_models_pytorch as smp
    except ImportError as error:
        raise RuntimeError(
            "Please install segmentation-models-pytorch to use this hull model: "
            "pip install segmentation-models-pytorch"
        ) from error
    arch_lower = arch.lower()
    encoder_weights = "imagenet" if pretrained else None
    if arch_lower == "unet":
        model = smp.Unet(encoder_name=encoder, encoder_weights=encoder_weights, classes=num_classes)
    elif arch_lower == "unetplusplus" or arch_lower == "unet++":
        model = smp.UnetPlusPlus(encoder_name=encoder, encoder_weights=encoder_weights, classes=num_classes)
    elif arch_lower == "deeplabv3":
        model = smp.DeepLabV3(encoder_name=encoder, encoder_weights=encoder_weights, classes=num_classes)
    elif arch_lower == "deeplabv3plus" or arch_lower == "deeplabv3+":
        model = smp.DeepLabV3Plus(encoder_name=encoder, encoder_weights=encoder_weights, classes=num_classes)
    elif arch_lower == "fpn":
        model = smp.FPN(encoder_name=encoder, encoder_weights=encoder_weights, classes=num_classes)
    else:
        raise ValueError(f"Unknown smp architecture: {arch!r}")
    return _SmpWrapper(model)

