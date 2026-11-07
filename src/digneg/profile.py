"""Calibration profile model and 16-bit curve / LUT math for digital negatives."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.interpolate import PchipInterpolator


INK_MODES = {
    "grayscale": {
        "label": "Standard Monochrome (K / Grayscale)",
        "description": "16-bit grayscale output. Best for pigment inks or standard B&W printing.",
    },
    "pyro_amber": {
        "label": "Silver Gelatin UV/Blue Blocker (Pyro Amber R+Y+K)",
        "description": (
            "Maximum blue/UV opacity for home dye-ink printers on Silver Gelatin paper. "
            "Combines Black, Yellow, and Red/Magenta dye channels to block blue/UV light "
            "without puddling ink on OHP film."
        ),
    },
    "ruby_red": {
        "label": "Safelight Ruby Density (Deep Red + Black)",
        "description": (
            "Uses rich red/yellow warmth in high-density regions so highlights act like a "
            "built-in safelight mask against blue-sensitive silver gelatin emulsion."
        ),
    },
}


@dataclass
class CalibrationProfile:
    """Stores step-wedge calibration parameters for a specific printer + OHP + paper combo."""

    name: str
    steps_total: int = 21
    black_step: float = 0.0
    white_step: float = 20.0
    midtone_step: Optional[float] = None
    gamma: float = 1.0
    ink_mode: str = "pyro_amber"
    flip_horizontal: bool = True
    notes: str = ""
    custom_points: List[Tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.steps_total < 2 or self.steps_total > 101:
            raise ValueError("steps_total must be between 2 and 101.")
        max_idx = float(self.steps_total - 1)
        if not (0.0 <= self.black_step < self.white_step <= max_idx):
            raise ValueError(
                f"Require 0 <= black_step ({self.black_step}) < white_step ({self.white_step}) <= {max_idx}"
            )
        if self.midtone_step is not None:
            if not (self.black_step < self.midtone_step < self.white_step):
                raise ValueError(
                    f"midtone_step ({self.midtone_step}) must be strictly between "
                    f"black_step ({self.black_step}) and white_step ({self.white_step})."
                )
        if self.gamma <= 0.05 or self.gamma > 5.0:
            raise ValueError("gamma must be between 0.05 and 5.0.")
        if self.ink_mode not in INK_MODES:
            raise ValueError(f"ink_mode must be one of {list(INK_MODES.keys())}")

    @property
    def d_min_fraction(self) -> float:
        """Normalized minimum ink density (0.0 to 1.0) that still produces pure paper black (Dmax)."""
        return float(self.black_step) / float(self.steps_total - 1)

    @property
    def d_max_fraction(self) -> float:
        """Normalized ink density (0.0 to 1.0) that first produces pure paper white (Dmin)."""
        return float(self.white_step) / float(self.steps_total - 1)

    def evaluate_ink_density(self, neg_tone: np.ndarray) -> np.ndarray:
        """Map positive luminance / tone x in [0.0, 1.0] to calibrated ink density D_out in [0.0, 1.0].

        - x = 0.0 (original shadow -> print black) maps to d_min_fraction (black_step)
        - x = 1.0 (original highlight -> print white) maps to d_max_fraction (white_step)
        """
        x = np.clip(np.asarray(neg_tone, dtype=np.float64), 0.0, 1.0)

        # 1. Apply gamma shaping first if gamma != 1.0
        if abs(self.gamma - 1.0) > 1e-6:
            x = np.power(x, 1.0 / self.gamma)

        # 2. Apply monotonic PCHIP spline if midtone_step or custom_points are provided
        d_min = self.d_min_fraction
        d_max = self.d_max_fraction

        if self.custom_points:
            pts = sorted(self.custom_points, key=lambda p: p[0])
            px = [0.0] + [float(p[0]) for p in pts if 0.0 < p[0] < 1.0] + [1.0]
            py = [d_min] + [
                d_min + float(p[1]) * (d_max - d_min) for p in pts if 0.0 < p[0] < 1.0
            ] + [d_max]
            interp = PchipInterpolator(px, py)
            return np.clip(interp(x), 0.0, 1.0)

        if self.midtone_step is not None:
            d_mid = float(self.midtone_step) / float(self.steps_total - 1)
            interp = PchipInterpolator([0.0, 0.5, 1.0], [d_min, d_mid, d_max])
            return np.clip(interp(x), 0.0, 1.0)

        # Linear remap between calibrated black point and white point
        return np.clip(d_min + x * (d_max - d_min), 0.0, 1.0)

    def build_16bit_lut(self) -> np.ndarray:
        """Precompute a 65536-entry uint16 lookup table mapping quantized 16-bit luminance to output pixels.

        Returns an array of shape (65536,) for grayscale or (65536, 3) for colorized spectral modes.
        """
        domain = np.linspace(0.0, 1.0, 65536, dtype=np.float64)
        ink_density = self.evaluate_ink_density(domain)
        return density_to_16bit_pixels(ink_density, ink_mode=self.ink_mode)

    def build_curve_preview_data(self, num_samples: int = 256) -> Dict[str, List[float]]:
        """Return x/y curve arrays for interactive UI plotting."""
        pos_in = np.linspace(0.0, 1.0, num_samples)
        cal_density = self.evaluate_ink_density(pos_in)
        neg_pixel_val = 1.0 - cal_density
        return {
            "positive_input_pct": [round(float(v) * 100.0, 2) for v in pos_in],
            "uncalibrated_ink_pct": [round(float(v) * 100.0, 2) for v in pos_in],
            "calibrated_ink_pct": [round(float(v) * 100.0, 2) for v in cal_density],
            "negative_pixel_pct": [round(float(v) * 100.0, 2) for v in neg_pixel_val],
            "black_pct": round(self.d_min_fraction * 100.0, 2),
            "white_pct": round(self.d_max_fraction * 100.0, 2),
        }

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CalibrationProfile":
        valid_keys = {
            "name",
            "steps_total",
            "black_step",
            "white_step",
            "midtone_step",
            "gamma",
            "ink_mode",
            "flip_horizontal",
            "notes",
            "custom_points",
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        if filtered.get("midtone_step") == "":
            filtered["midtone_step"] = None
        return cls(**filtered)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "CalibrationProfile":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


def density_to_16bit_pixels(ink_density: np.ndarray, ink_mode: str = "grayscale") -> np.ndarray:
    """Convert normalized ink density D in [0.0, 1.0] into 16-bit pixel values (uint16).

    In digital print files:
    - D = 0.0 (0% ink, clear OHP film) -> pixel value 65535 (white)
    - D = 1.0 (100% ink, opaque negative) -> pixel value 0 (black) or UV-blocking color mix
    """
    d = np.clip(np.asarray(ink_density, dtype=np.float64), 0.0, 1.0)

    if ink_mode == "grayscale":
        val = np.rint((1.0 - d) * 65535.0).astype(np.uint16)
        return val

    if ink_mode == "pyro_amber":
        r_trans = np.clip(1.0 - 0.92 * np.power(d, 1.05), 0.0, 1.0)
        g_trans = np.clip(1.0 - 0.98 * np.power(d, 0.85), 0.0, 1.0)
        b_trans = np.clip(1.0 - 1.00 * np.power(d, 0.60), 0.0, 1.0)
        rgb = np.stack([r_trans, g_trans, b_trans], axis=-1)
        return np.rint(rgb * 65535.0).astype(np.uint16)

    if ink_mode == "ruby_red":
        r_trans = np.clip(1.0 - 0.85 * np.power(d, 1.25), 0.0, 1.0)
        g_trans = np.clip(1.0 - 1.00 * np.power(d, 0.75), 0.0, 1.0)
        b_trans = np.clip(1.0 - 1.00 * np.power(d, 0.50), 0.0, 1.0)
        rgb = np.stack([r_trans, g_trans, b_trans], axis=-1)
        return np.rint(rgb * 65535.0).astype(np.uint16)

    raise ValueError(f"Unknown ink_mode: {ink_mode}")


DEFAULT_PROFILE = CalibrationProfile(
    name="ilford_mg_rc_default",
    steps_total=21,
    black_step=2.0,
    white_step=17.0,
    midtone_step=9.5,
    gamma=1.0,
    ink_mode="pyro_amber",
    flip_horizontal=True,
    notes="Default Silver Gelatin starter profile (21-step wedge: Dmax at step 2 / 10% ink, Dmin paper white at step 17 / 85% ink, Pyro-Amber UV/Blue blocking dye mode).",
)
