"""Core 16-bit Digital Negative conversion pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image

from digneg.io_utils import load_image_normalized, save_tiff_16bit, to_luminance
from digneg.profile import CalibrationProfile, density_to_16bit_pixels


def process_image_to_negative(
    image_norm: np.ndarray,
    profile: CalibrationProfile,
    override_flip: Optional[bool] = None,
) -> np.ndarray:
    """Convert a normalized positive image array [0.0, 1.0] into a 16-bit digital negative (uint16).

    Steps:
    1. Convert RGB to perceptual luminance Y in [0.0, 1.0] (if 3-channel).
    2. Invert positive luminance to negative space: N = 1.0 - Y
       (so positive black shadow Y=0 -> N=0 -> black_step Dmax;
        positive white highlight Y=1 -> N=1 -> white_step Dmin).
    3. Apply profile calibration curve (black_step, white_step, midtone_step, gamma).
    4. Convert calibrated ink density D_out in [0.0, 1.0] to 16-bit pixel values
       (Grayscale or UV/Blue-blocking dye color LUT).
    5. Apply horizontal flip for emulsion-to-emulsion contact printing.
    """
    lum = to_luminance(image_norm)

    # Note on inversion direction:
    # In a positive image:
    # - Y = 0.0 is shadow (we want the final silver gelatin print to be BLACK / Dmax,
    #   which requires CLEAR film / minimum ink density = black_step).
    # - Y = 1.0 is highlight (we want the final silver gelatin print to be WHITE / Dmin,
    #   which requires OPAQUE film / maximum ink density = white_step).
    # Since profile.evaluate_ink_density(x) maps x=0.0 -> black_step (min ink) and x=1.0 -> white_step (max ink),
    # x is literally the positive luminance Y!
    # Wait: let's double check this!
    # When x = 0.0 (shadow on positive), evaluate_ink_density(0.0) = d_min (e.g. 10% ink).
    # Then density_to_16bit_pixels(0.10) = (1.0 - 0.10) * 65535 = 90% white pixel on the negative file
    # (which means the printer lays down only 10% ink = almost clear OHP film -> prints BLACK on paper!).
    # When x = 1.0 (highlight on positive), evaluate_ink_density(1.0) = d_max (e.g. 85% ink).
    # Then density_to_16bit_pixels(0.85) = (1.0 - 0.85) * 65535 = 15% dark pixel on the negative file
    # (which means the printer lays down 85% ink = opaque OHP film -> prints WHITE on paper!).
    # That means:
    # - Positive luminance Y = 0.0 -> Ink density D = d_min -> Negative pixel value = HIGH (light/clear on screen, inverted!)
    # - Positive luminance Y = 1.0 -> Ink density D = d_max -> Negative pixel value = LOW (dark/opaque on screen, inverted!)
    # So `density_to_16bit_pixels(evaluate_ink_density(lum))` ALREADY performs the inversion (1.0 - D)!
    ink_density = profile.evaluate_ink_density(lum)
    neg_16bit = density_to_16bit_pixels(ink_density, ink_mode=profile.ink_mode)

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
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS)
    return img
