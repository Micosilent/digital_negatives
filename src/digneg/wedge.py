"""Step wedge and ink-opacity test chart generator for silver gelatin digital negatives."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from digneg.io_utils import save_tiff_16bit
from digneg.profile import INK_MODES, CalibrationProfile, density_to_16bit_pixels


FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def _load_font(size_px: int) -> ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=max(10, size_px))
            except Exception:
                continue
    return ImageFont.load_default()


def generate_step_wedge_array(
    steps: int = 21,
    width_inches: float = 8.0,
    height_inches: float = 2.5,
    dpi: int = 300,
    ink_mode: str = "pyro_amber",
    flip_horizontal: bool = True,
    profile: Optional[CalibrationProfile] = None,
    include_ink_comparison: bool = False,
) -> np.ndarray:
    """Generate a 16-bit step wedge array (uint16) with Dmax/Dmin reference rails and step labels.

    Layout (top to bottom):
    1. Header banner with step wedge metadata (steps, ink mode, profile name)
    2. Top Reference Rail: Half 0% ink (Paper Black Dmax ref) / Half 100% ink (Paper White Dmin ref)
    3. Main Step Wedge patches (Step 0 to Step N-1) with high-contrast labels & percentage values
    4. Bottom Reference Rail: Continuous 0% and 100% comparison strips adjacent to every step
    5. (Optional) Multi-Ink Comparison Strip comparing Grayscale vs Pyro Amber vs Ruby Red
    """
    if steps < 2 or steps > 101:
        raise ValueError("steps must be between 2 and 101.")
    if not (1.0 <= width_inches <= 24.0):
        raise ValueError("width_inches must be between 1.0 and 24.0.")
    if not (0.5 <= height_inches <= 12.0):
        raise ValueError("height_inches must be between 0.5 and 12.0.")
    if not (72 <= dpi <= 1440):
        raise ValueError("dpi must be between 72 and 1440.")
    if ink_mode not in INK_MODES:
        raise ValueError(f"ink_mode must be one of {list(INK_MODES.keys())}")

    width_px = max(400, int(round(width_inches * dpi)))
    height_px = max(180, int(round(height_inches * dpi)))

    # Create a normalized ink density map D in [0.0, 1.0], where:
    # D = 0.0 -> 0% ink (clear film -> prints PURE BLACK on silver gelatin)
    # D = 1.0 -> 100% ink (opaque negative -> prints PAPER WHITE on silver gelatin)
    density_map = np.zeros((height_px, width_px), dtype=np.float64)

    margin_x = int(round(0.25 * dpi))
    margin_y = int(round(0.18 * dpi))
    usable_w = width_px - 2 * margin_x
    usable_h = height_px - 2 * margin_y

    header_h = int(round(usable_h * 0.16))
    rail_h = int(round(usable_h * 0.12))
    comp_h = int(round(usable_h * 0.22)) if include_ink_comparison else 0
    patches_h = usable_h - header_h - 2 * rail_h - comp_h

    y_header = margin_y
    y_top_rail = y_header + header_h
    y_patches = y_top_rail + rail_h
    y_bot_rail = y_patches + patches_h
    y_comp = y_bot_rail + rail_h

    # Fill outer border with medium-high density (85% ink) so edges print light gray
    density_map[:, :] = 0.85
    # Inner working area clear (0% ink) initially
    density_map[margin_y : margin_y + usable_h, margin_x : margin_x + usable_w] = 0.92

    # Top Reference Rail: continuous 0% ink strip (Dmax black reference) directly above steps
    density_map[y_top_rail : y_top_rail + rail_h, margin_x : margin_x + usable_w] = 0.0

    # Bottom Reference Rail: continuous 100% ink strip (Dmin paper white reference) directly below steps
    density_map[y_bot_rail : y_bot_rail + rail_h, margin_x : margin_x + usable_w] = 1.0

    # Step densities
    linear_densities = np.linspace(0.0, 1.0, steps)
    if profile is not None:
        step_densities = profile.evaluate_ink_density(linear_densities)
    else:
        step_densities = linear_densities

    # Draw main step patches
    step_edges = np.linspace(margin_x, margin_x + usable_w, steps + 1, dtype=int)
    for i in range(steps):
        x0, x1 = step_edges[i], step_edges[i + 1]
        d_val = float(step_densities[i])
        density_map[y_patches : y_patches + patches_h, x0:x1] = d_val

        # Add small alternating reference notches at top/bottom of each patch for visual boundary check
        notch_h = max(4, int(round(patches_h * 0.06)))
        density_map[y_patches : y_patches + notch_h, x0 : x0 + 2] = 1.0 if d_val < 0.5 else 0.0
        density_map[y_patches + patches_h - notch_h : y_patches + patches_h, x0 : x0 + 2] = (
            1.0 if d_val < 0.5 else 0.0
        )

    # Render crisp text overlay (step numbers, percentages, rail labels) using an 8-bit mask
    text_layer = Image.new("L", (width_px, height_px), color=128)
    draw = ImageDraw.Draw(text_layer)

    font_title = _load_font(int(round(header_h * 0.45)))
    font_rail = _load_font(int(round(rail_h * 0.52)))
    font_step = _load_font(int(round( min(patches_h * 0.18, (usable_w / steps) * 0.48) )))
    font_pct = _load_font(int(round( min(patches_h * 0.13, (usable_w / steps) * 0.36) )))

    mode_title = INK_MODES[ink_mode]["label"]
    prof_label = f"Profile: {profile.name}" if profile else "Uncalibrated Linear Wedge"
    title_text = f"DIGNEG {steps}-STEP CALIBRATION WEDGE  |  {mode_title}  |  {prof_label}"
    draw.text((margin_x + 8, y_header + int(header_h * 0.2)), title_text, fill=0, font=font_title)

    # Top rail label (0% ink -> prints Dmax Pure Black)
    draw.text(
        (margin_x + 8, y_top_rail + int(rail_h * 0.18)),
        "Dmax REFERENCE RAIL (0% Ink / Clear Film -> Prints Maximum Paper Black)",
        fill=255,
        font=font_rail,
    )
    # Bottom rail label (100% ink -> prints Dmin Paper White)
    draw.text(
        (margin_x + 8, y_bot_rail + int(rail_h * 0.18)),
        "Dmin REFERENCE RAIL (100% Ink / Max Opacity -> Prints Paper Base White)",
        fill=0,
        font=font_rail,
    )

    # Step numbers and percentages inside each patch
    for i in range(steps):
        x0, x1 = step_edges[i], step_edges[i + 1]
        cx = (x0 + x1) // 2
        d_val = float(step_densities[i])
        pct_int = int(round(linear_densities[i] * 100))

        # High contrast text color: if patch ink density < 0.5, use 1.0 (white on print); else 0.0 (black on print)
        text_fill = 255 if d_val < 0.5 else 0

        step_str = f"{i}"
        pct_str = f"{pct_int}%"

        # Center text horizontally
        s_bbox = draw.textbbox((0, 0), step_str, font=font_step)
        s_w = s_bbox[2] - s_bbox[0]
        draw.text(
            (cx - s_w // 2, y_patches + int(patches_h * 0.22)),
            step_str,
            fill=text_fill,
            font=font_step,
        )

        p_bbox = draw.textbbox((0, 0), pct_str, font=font_pct)
        p_w = p_bbox[2] - p_bbox[0]
        draw.text(
            (cx - p_w // 2, y_patches + int(patches_h * 0.58)),
            pct_str,
            fill=text_fill,
            font=font_pct,
        )

    # Convert density map to 16-bit pixels using selected ink_mode
    out_16bit = density_to_16bit_pixels(density_map, ink_mode=ink_mode)

    # If include_ink_comparison is requested, render a 3-row mini strip at y_comp comparing all 3 ink modes
    if include_ink_comparison and comp_h > 0:
        if out_16bit.ndim == 2:
            out_16bit = np.stack([out_16bit, out_16bit, out_16bit], axis=-1)

        modes_list = ["grayscale", "pyro_amber", "ruby_red"]
        row_h = comp_h // len(modes_list)
        for m_idx, m_key in enumerate(modes_list):
            ry0 = y_comp + m_idx * row_h
            ry1 = ry0 + row_h if m_idx < len(modes_list) - 1 else y_comp + comp_h
            row_density = np.zeros((ry1 - ry0, width_px), dtype=np.float64) + 0.85
            for i in range(steps):
                x0, x1 = step_edges[i], step_edges[i + 1]
                row_density[:, x0:x1] = float(step_densities[i])
            row_pixels = density_to_16bit_pixels(row_density, ink_mode=m_key)
            if row_pixels.ndim == 2:
                row_pixels = np.stack([row_pixels, row_pixels, row_pixels], axis=-1)
            out_16bit[ry0:ry1, :, :] = row_pixels

            draw.text(
                (margin_x + 6, ry0 + max(2, int(row_h * 0.15))),
                f"INK TEST: {m_key.upper()}",
                fill=0,
                font=font_pct,
            )

    # Apply text mask onto 16-bit output
    text_arr = np.array(text_layer, dtype=np.uint8)
    high_density_mask = text_arr > 200  # Text that should be 100% ink (0 pixel value)
    low_density_mask = text_arr < 55    # Text that should be 0% ink (65535 pixel value)

    if out_16bit.ndim == 2:
        out_16bit[high_density_mask] = 0
        out_16bit[low_density_mask] = 65535
    else:
        max_ink_rgb = density_to_16bit_pixels(np.array([1.0]), ink_mode=ink_mode)[0]
        out_16bit[high_density_mask] = max_ink_rgb
        out_16bit[low_density_mask] = 65535

    # Flip horizontally if configured (so text & step numbers read right-way-round when printed emulsion-down)
    if flip_horizontal:
        out_16bit = np.fliplr(out_16bit)

    return out_16bit


def save_step_wedge(
    output_path: str | Path,
    steps: int = 21,
    width_inches: float = 8.0,
    height_inches: float = 2.5,
    dpi: int = 300,
    ink_mode: str = "pyro_amber",
    flip_horizontal: bool = True,
    profile: Optional[CalibrationProfile] = None,
    include_ink_comparison: bool = False,
) -> Path:
    """Generate and save a 16-bit TIFF step wedge to disk."""
    arr = generate_step_wedge_array(
        steps=steps,
        width_inches=width_inches,
        height_inches=height_inches,
        dpi=dpi,
        ink_mode=ink_mode,
        flip_horizontal=flip_horizontal,
        profile=profile,
        include_ink_comparison=include_ink_comparison,
    )
    return save_tiff_16bit(output_path, arr, dpi=(float(dpi), float(dpi)))
