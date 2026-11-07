"""16-bit and 8-bit image I/O utilities for TIFF, PNG, and JPEG."""

from __future__ import annotations

import io
from pathlib import Path
import struct
from typing import BinaryIO, Tuple, Union

import numpy as np
from PIL import Image

# Guard against decompression bombs when exposed as a web service (120 megapixels max)
Image.MAX_IMAGE_PIXELS = 120_000_000

ImageSource = Union[str, Path, bytes, BinaryIO]


def load_image_normalized(source: ImageSource) -> Tuple[np.ndarray, Tuple[float, float]]:
    """Load a PNG, JPEG, or TIFF image from path, bytes, or stream into a float64 array in [0.0, 1.0].

    Supports 8-bit and 16-bit grayscale, RGB, and RGBA images.
    Returns (luminance_or_rgb_array, (dpi_x, dpi_y)).
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")
        stream_or_path: Union[Path, BinaryIO] = path
    elif isinstance(source, bytes):
        stream_or_path = io.BytesIO(source)
    else:
        stream_or_path = source

    with Image.open(stream_or_path) as img:
        dpi = img.info.get("dpi", (300.0, 300.0))
        dpi = (float(dpi[0]), float(dpi[1]))

        # Handle uncompressed 16-bit RGB/RGBA TIFFs directly (Pillow truncates 16-bit RGB to 8-bit)
        if img.format == "TIFF" and hasattr(img, "tag_v2"):
            bps = img.tag_v2.get(258)
            comp = img.tag_v2.get(259, 1)
            planar = img.tag_v2.get(284, 1)
            offsets = img.tag_v2.get(273)
            byte_counts = img.tag_v2.get(279)
            if (
                comp == 1
                and planar == 1
                and bps in ((16, 16, 16), (16, 16, 16, 16))
                and offsets
                and byte_counts
            ):
                w, h = img.size
                ch = len(bps)
                fp = img.fp
                fp.seek(0)
                magic = fp.read(2)
                endian = ">u2" if magic == b"MM" else "<u2"
                strips = []
                for off, cnt in zip(offsets, byte_counts):
                    fp.seek(off)
                    strips.append(fp.read(cnt))
                raw_pcm = b"".join(strips)
                expected_bytes = w * h * ch * 2
                if len(raw_pcm) >= expected_bytes:
                    arr16 = np.frombuffer(raw_pcm[:expected_bytes], dtype=endian).reshape((h, w, ch))
                    arr_f = arr16[:, :, :3].astype(np.float64) / 65535.0
                    return np.clip(arr_f, 0.0, 1.0), dpi

        # Handle 16-bit grayscale modes directly before any conversion
        if img.mode in ("I;16", "I;16B", "I;16L", "I"):
            arr = np.array(img, dtype=np.float64)
            if img.mode == "I" and arr.max() <= 255.0:
                scale = 255.0
            else:
                scale = 65535.0
            return np.clip(arr / scale, 0.0, 1.0), dpi

        # Standard RGB / RGBA / L / P modes
        if img.mode == "RGBA":
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


def encode_tiff_16bit_bytes(
    arr_uint16: np.ndarray,
    dpi: Tuple[float, float] = (300.0, 300.0),
) -> bytes:
    """Encode a 2D (grayscale) or 3D (RGB) uint16 array as an uncompressed 16-bit TIFF in memory."""
    if arr_uint16.dtype != np.uint16:
        raise ValueError(f"Expected uint16 array, got {arr_uint16.dtype}")

    if arr_uint16.ndim == 2:
        buf = io.BytesIO()
        img = Image.fromarray(arr_uint16)
        img.save(buf, format="TIFF", dpi=dpi)
        return buf.getvalue()

    if arr_uint16.ndim == 3 and arr_uint16.shape[2] == 3:
        return _encode_rgb_16bit_tiff_bytes(arr_uint16, dpi=dpi)

    raise ValueError(f"Unsupported array shape for 16-bit TIFF: {arr_uint16.shape}")


def save_tiff_16bit(
    path: str | Path,
    arr_uint16: np.ndarray,
    dpi: Tuple[float, float] = (300.0, 300.0),
) -> Path:
    """Save a 2D (grayscale) or 3D (RGB) uint16 array as an uncompressed 16-bit TIFF with DPI metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = encode_tiff_16bit_bytes(arr_uint16, dpi=dpi)
    path.write_bytes(raw_bytes)
    return path


def _encode_rgb_16bit_tiff_bytes(
    arr: np.ndarray,
    dpi: Tuple[float, float] = (300.0, 300.0),
) -> bytes:
    """Encode a 3-channel uint16 numpy array (H, W, 3) to uncompressed baseline 16-bit RGB TIFF bytes."""
    height, width, channels = arr.shape
    assert channels == 3

    pixel_bytes = np.ascontiguousarray(arr, dtype="<u2").tobytes()
    strip_byte_count = len(pixel_bytes)

    num_tags = 12
    ifd_offset = 8
    ifd_size = 2 + num_tags * 12 + 4
    extra_offset = ifd_offset + ifd_size

    bps_offset = extra_offset
    bps_bytes = struct.pack("<HHH", 16, 16, 16)

    xres_offset = bps_offset + len(bps_bytes)
    xres_bytes = struct.pack("<II", int(round(dpi[0] * 1000)), 1000)

    yres_offset = xres_offset + len(xres_bytes)
    yres_bytes = struct.pack("<II", int(round(dpi[1] * 1000)), 1000)

    strip_offset = yres_offset + len(yres_bytes)

    def tag_entry(tag: int, dtype: int, count: int, value_or_offset: int) -> bytes:
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

    return b"".join([header, ifd, bps_bytes, xres_bytes, yres_bytes, pixel_bytes])
