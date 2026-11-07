"""16-bit and 8-bit image I/O utilities for TIFF, PNG, and JPEG."""

from __future__ import annotations

from pathlib import Path
import struct
from typing import Tuple

import numpy as np
from PIL import Image


def load_image_normalized(path: str | Path) -> Tuple[np.ndarray, Tuple[float, float]]:
    """Load a PNG, JPEG, or TIFF image into a normalized float64 array in [0.0, 1.0].

    Supports 8-bit and 16-bit grayscale, RGB, and RGBA images.
    Returns (luminance_or_rgb_array, (dpi_x, dpi_y)).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")

    with Image.open(path) as img:
        dpi = img.info.get("dpi", (300.0, 300.0))
        dpi = (float(dpi[0]), float(dpi[1]))

        # Handle 16-bit grayscale modes directly before any conversion
        if img.mode in ("I;16", "I;16B", "I;16L", "I"):
            arr = np.array(img, dtype=np.float64)
            # Determine bit depth scale
            if img.mode == "I" and arr.max() <= 255.0:
                scale = 255.0
            else:
                scale = 65535.0
            return np.clip(arr / scale, 0.0, 1.0), dpi

        # Standard RGB / RGBA / L / P modes
        if img.mode == "RGBA":
            # Composite over white background if alpha channel exists
            bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(bg, img).convert("RGB")
        elif img.mode not in ("L", "RGB"):
            img = img.convert("RGB")

        arr = np.array(img, dtype=np.float64)
        scale = 65535.0 if arr.max() > 255.0 else 255.0
        return np.clip(arr / scale, 0.0, 1.0), dpi


def to_luminance(arr: np.ndarray) -> np.ndarray:
    """Convert a normalized image array (2D grayscale or 3D RGB) to 2D luminance in [0.0, 1.0]."""
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3 and arr.shape[2] >= 3:
        # Rec. 709 perceptual luminance weights
        lum = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
        return np.clip(lum, 0.0, 1.0)
    raise ValueError(f"Unsupported image array shape: {arr.shape}")


def save_tiff_16bit(
    path: str | Path,
    arr_uint16: np.ndarray,
    dpi: Tuple[float, float] = (300.0, 300.0),
) -> Path:
    """Save a 2D (grayscale) or 3D (RGB) uint16 array as an uncompressed 16-bit TIFF with DPI metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if arr_uint16.dtype != np.uint16:
        raise ValueError(f"Expected uint16 array, got {arr_uint16.dtype}")

    if arr_uint16.ndim == 2:
        # Pillow natively writes 2D uint16 as 16-bit grayscale TIFF (mode I;16)
        img = Image.fromarray(arr_uint16)
        img.save(path, format="TIFF", dpi=dpi)
        return path

    if arr_uint16.ndim == 3 and arr_uint16.shape[2] == 3:
        # Write baseline uncompressed 16-bit RGB TIFF (little-endian)
        _write_rgb_16bit_tiff(path, arr_uint16, dpi=dpi)
        return path

    raise ValueError(f"Unsupported array shape for 16-bit TIFF: {arr_uint16.shape}")


def _write_rgb_16bit_tiff(
    path: Path,
    arr: np.ndarray,
    dpi: Tuple[float, float] = (300.0, 300.0),
) -> None:
    """Write a 3-channel uint16 numpy array (H, W, 3) to an uncompressed baseline 16-bit RGB TIFF."""
    height, width, channels = arr.shape
    assert channels == 3

    # Ensure little-endian uint16 contiguous bytes
    pixel_bytes = np.ascontiguousarray(arr, dtype="<u2").tobytes()
    strip_byte_count = len(pixel_bytes)

    # TIFF Header (8 bytes): 'II' (little-endian), magic 42, offset to IFD (8)
    # Followed by IFD entries, extra values (BitsPerSample, XRes, YRes), and pixel data
    num_tags = 12
    ifd_offset = 8
    ifd_size = 2 + num_tags * 12 + 4
    extra_offset = ifd_offset + ifd_size

    # Extra data layout:
    # 1. BitsPerSample: 3 * uint16 (6 bytes) -> (16, 16, 16)
    bps_offset = extra_offset
    bps_bytes = struct.pack("<HHH", 16, 16, 16)

    # 2. XResolution: RATIONAL (2 * uint32 = 8 bytes)
    xres_offset = bps_offset + len(bps_bytes)
    xres_bytes = struct.pack("<II", int(round(dpi[0] * 1000)), 1000)

    # 3. YResolution: RATIONAL (2 * uint32 = 8 bytes)
    yres_offset = xres_offset + len(xres_bytes)
    yres_bytes = struct.pack("<II", int(round(dpi[1] * 1000)), 1000)

    # Pixel data starts immediately after extra values
    strip_offset = yres_offset + len(yres_bytes)

    def tag_entry(tag: int, dtype: int, count: int, value_or_offset: int) -> bytes:
        # dtype: 3 = SHORT (uint16), 4 = LONG (uint32), 5 = RATIONAL
        return struct.pack("<HHII", tag, dtype, count, value_or_offset)

    tags = [
        tag_entry(256, 4, 1, width),             # ImageWidth
        tag_entry(257, 4, 1, height),            # ImageLength
        tag_entry(258, 3, 3, bps_offset),        # BitsPerSample (16, 16, 16)
        tag_entry(259, 3, 1, 1),                 # Compression = 1 (None)
        tag_entry(262, 3, 1, 2),                 # PhotometricInterpretation = 2 (RGB)
        tag_entry(273, 4, 1, strip_offset),      # StripOffsets
        tag_entry(277, 3, 1, 3),                 # SamplesPerPixel = 3
        tag_entry(278, 4, 1, height),            # RowsPerStrip = height
        tag_entry(279, 4, 1, strip_byte_count),  # StripByteCounts
        tag_entry(282, 5, 1, xres_offset),       # XResolution
        tag_entry(283, 5, 1, yres_offset),       # YResolution
        tag_entry(296, 3, 1, 2),                 # ResolutionUnit = 2 (Inch)
    ]

    header = struct.pack("<2sHI", b"II", 42, ifd_offset)
    ifd = struct.pack("<H", num_tags) + b"".join(tags) + struct.pack("<I", 0)

    with open(path, "wb") as f:
        f.write(header)
        f.write(ifd)
        f.write(bps_bytes)
        f.write(xres_bytes)
        f.write(yres_bytes)
        f.write(pixel_bytes)
