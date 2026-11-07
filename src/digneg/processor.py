"""Core 16-bit Digital Negative conversion pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from digneg.io_utils import load_image_normalized, save_tiff_16bit, to_luminance
from digneg.profile import CalibrationProfile


def downsample_luminance(lum: np.ndarray, max_dim: int = 720) -> np.ndarray:
    """Downsample a 2D normalized luminance array [0.0, 1.0] to fit within max_dim for fast previewing."""
    h, w = lum.shape[:2]
    longest = max(h, w)
    if longest <= max_dim:
        return lum

    scale = max_dim / float(longest)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))

    # Use 32-bit float PIL image resize to preserve tonal precision while downsampling
    img = Image.fromarray(np.clip(lum, 0.0, 1.0).astype(np.float32), mode="F")
    resample_filter = Image.Resampling.BILINEAR if hasattr(Image, "Resampling") else Image.BILINEAR
    img_small = img.resize((new_w, new_h), resample=resample_filter)
    return np.clip(np.asarray(img_small, dtype=np.float64), 0.0, 1.0)


def process_image_to_negative(
    image_norm: np.ndarray,
    profile: CalibrationProfile,
    override_flip: Optional[bool] = None,
) -> np.ndarray:
    """Convert a normalized positive image array [0.0, 1.0] into a 16-bit digital negative (uint16).

    Uses a precomputed 65,536-entry 16-bit lookup table (`profile.build_16bit_lut()`)
    for O(1) per-pixel curve and spectral ink mapping.
    """
    lum = to_luminance(image_norm)
    lum_u16 = np.rint(np.clip(lum, 0.0, 1.0) * 65535.0).astype(np.uint16)

    lut = profile.build_16bit_lut()
    neg_16bit = lut[lum_u16]

    do_flip = profile.flip_horizontal if override_flip is None else override_flip
    if do_flip:
        neg_16bit = np.fliplr(neg_16bit)

    return neg_16bit


def convert_image_file(
    input_path: str | Path,
    output_path: str | Path,
    profile: CalibrationProfile,
    override_flip: Optional[bool] = None,
    override_dpi: Optional[float] = None,
) -> Path:
    """Load an image file (PNG, JPEG, TIFF), convert to a 16-bit calibrated negative TIFF, and save."""
    arr_norm, dpi = load_image_normalized(input_path)
    if override_dpi is not None:
        dpi = (float(override_dpi), float(override_dpi))

    neg_16bit = process_image_to_negative(
        arr_norm,
        profile=profile,
        override_flip=override_flip,
    )
    return save_tiff_16bit(output_path, neg_16bit, dpi=dpi)


def array_16bit_to_8bit_preview_pil(arr_16bit: np.ndarray, max_dim: int = 900) -> Image.Image:
    """Convert a 16-bit uint16 grayscale or RGB array to an 8-bit PIL Image for web/UI preview."""
    arr_8 = np.clip(np.rint(arr_16bit.astype(np.float64) / 257.0), 0, 255).astype(np.uint8)
    img = Image.fromarray(arr_8)
    if max(img.size) > max_dim:
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        img.thumbnail((max_dim, max_dim), resample)
    return img
