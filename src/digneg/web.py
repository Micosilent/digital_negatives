"""Stateless HTTP server and API routes for the digneg web interface."""

from __future__ import annotations

import base64
import binascii
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import traceback
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse
import webbrowser

import numpy as np

from digneg.io_utils import encode_tiff_16bit_bytes, load_image_normalized, to_luminance
from digneg.processor import (
    array_16bit_to_8bit_preview_pil,
    downsample_luminance,
    process_image_to_negative,
)
from digneg.profile import DEFAULT_PROFILE, INK_MODES, CalibrationProfile
from digneg.wedge import generate_step_wedge_array


STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 100 * 1024 * 1024  # 100 MB upper bound on HTTP POST body


class PayloadTooLargeError(Exception):
    """Raised when Content-Length exceeds MAX_BODY_BYTES."""


def _pil_to_base64_png(pil_img) -> str:
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


class DigNegRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler serving static UI assets and stateless JSON/TIFF API endpoints."""

    timeout = 30

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _send_json(self, data: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_bytes(
        self,
        raw: bytes,
        content_type: str,
        filename: Optional[str] = None,
        status: int = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        if filename:
            safe_name = "".join(c for c in filename if c.isalnum() or c in ("_", "-", "."))
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        self.end_headers()
        self.wfile.write(raw)

    def _serve_static_file(self, rel_path: str) -> None:
        base_dir = STATIC_DIR.resolve()
        target = (base_dir / rel_path).resolve()
        if not target.is_relative_to(base_dir) or not target.is_file():
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return
        mime, _ = mimetypes.guess_type(str(target))
        content_type = mime or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type = f"{content_type}; charset=utf-8"
        self._send_bytes(target.read_bytes(), content_type)

    def _read_json_body(self) -> Dict[str, Any]:
        raw_len = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_len)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header.") from exc

        if length < 0:
            raise ValueError("Content-Length cannot be negative.")
        if length > MAX_BODY_BYTES:
            raise PayloadTooLargeError(
                f"Request payload ({length} bytes) exceeds {MAX_BODY_BYTES} byte limit."
            )

        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON.") from exc

        if not isinstance(parsed, dict):
            raise ValueError("JSON root must be an object.")
        return parsed

    def _parse_profile(self, payload: Dict[str, Any]) -> Optional[CalibrationProfile]:
        prof_data = payload.get("profile")
        if isinstance(prof_data, dict):
            return CalibrationProfile.from_dict(prof_data)
        return None

    def _handle_curve_preview(self, payload: Dict[str, Any]) -> None:
        prof = CalibrationProfile.from_dict(payload)
        self._send_json({"curve": prof.build_curve_preview_data()})

    def _handle_wedge_preview(self, payload: Dict[str, Any]) -> None:
        arr_16, _, _ = self._build_wedge_from_payload(payload)
        pil_prev = array_16bit_to_8bit_preview_pil(arr_16, max_dim=1100)
        self._send_json(
            {
                "preview": _pil_to_base64_png(pil_prev),
                "width": int(arr_16.shape[1]),
                "height": int(arr_16.shape[0]),
            }
        )

    def _handle_wedge_tiff(self, payload: Dict[str, Any]) -> None:
        arr_16, dpi, filename = self._build_wedge_from_payload(payload)
        tiff_bytes = encode_tiff_16bit_bytes(arr_16, dpi=(float(dpi), float(dpi)))
        self._send_bytes(tiff_bytes, content_type="image/tiff", filename=filename)

    def _build_wedge_from_payload(
        self, payload: Dict[str, Any]
    ) -> tuple[np.ndarray, int, str]:
        prof = self._parse_profile(payload)
        steps = int(payload.get("steps", 21))
        dpi = int(payload.get("dpi", 300))
        width_in = float(payload.get("width_inches", 8.0))
        height_in = float(payload.get("height_inches", 2.5))
        ink_mode = str(payload.get("ink_mode", "pyro_amber"))
        flip = bool(payload.get("flip_horizontal", True))
        comp = bool(payload.get("include_ink_comparison", False))

        arr_16 = generate_step_wedge_array(
            steps=steps,
            width_inches=width_in,
            height_inches=height_in,
            dpi=dpi,
            ink_mode=ink_mode,
            flip_horizontal=flip,
            profile=prof,
            include_ink_comparison=comp,
        )
        filename = f"wedge_{steps}step_{ink_mode}_16bit.tiff"
        return arr_16, dpi, filename

    def _decode_uploaded_image(
        self, payload: Dict[str, Any]
    ) -> tuple[np.ndarray, float, CalibrationProfile, Optional[bool]]:
        b64_data = payload.get("image_base64")
        if not b64_data or not isinstance(b64_data, str):
            raise ValueError("Missing image_base64 string.")

        try:
            raw_bytes = base64.b64decode(b64_data, validate=True)
        except binascii.Error as exc:
            raise ValueError("Invalid base64 image data.") from exc

        arr_norm, orig_dpi = load_image_normalized(raw_bytes)
        prof = self._parse_profile(payload) or CalibrationProfile(name="default_linear")
        if payload.get("override_ink_mode"):
            prof.ink_mode = str(payload["override_ink_mode"])
            prof.validate()

        override_flip = payload.get("override_flip")
        if override_flip is not None:
            override_flip = bool(override_flip)

        dpi_val = float(payload["override_dpi"]) if payload.get("override_dpi") else orig_dpi[0]
        if not (10.0 <= dpi_val <= 10000.0):
            raise ValueError("Output DPI must be between 10 and 10000.")

        return arr_norm, dpi_val, prof, override_flip

    def _handle_convert_preview(self, payload: Dict[str, Any]) -> None:
        arr_norm, dpi_val, prof, override_flip = self._decode_uploaded_image(payload)
        orig_h, orig_w = arr_norm.shape[:2]

        # Downsample luminance to preview dimensions prior to curve/LUT evaluation
        lum_full = to_luminance(arr_norm)
        lum_small = downsample_luminance(lum_full, max_dim=720)

        pos_16 = np.rint(lum_small * 65535.0).astype(np.uint16)
        neg_16 = process_image_to_negative(lum_small, profile=prof, override_flip=override_flip)

        pos_pil = array_16bit_to_8bit_preview_pil(pos_16, max_dim=720)
        neg_pil = array_16bit_to_8bit_preview_pil(neg_16, max_dim=720)
        self._send_json(
            {
                "positive_preview": _pil_to_base64_png(pos_pil),
                "negative_preview": _pil_to_base64_png(neg_pil),
                "width": int(orig_w),
                "height": int(orig_h),
                "dpi": int(round(dpi_val)),
                "ink_mode": prof.ink_mode,
            }
        )

    def _handle_convert_tiff(self, payload: Dict[str, Any]) -> None:
        arr_norm, dpi_val, prof, override_flip = self._decode_uploaded_image(payload)
        neg_16 = process_image_to_negative(arr_norm, profile=prof, override_flip=override_flip)
        tiff_bytes = encode_tiff_16bit_bytes(neg_16, dpi=(dpi_val, dpi_val))
        self._send_bytes(
            tiff_bytes,
            content_type="image/tiff",
            filename="digital_negative_16bit.tiff",
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._serve_static_file("index.html")
            return

        if parsed.path.startswith("/static/"):
            rel = parsed.path[len("/static/") :]
            self._serve_static_file(rel)
            return

        if parsed.path == "/api/defaults":
            self._send_json(
                {
                    "profiles": [DEFAULT_PROFILE.to_dict()],
                    "ink_modes": INK_MODES,
                }
            )
            return

        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        routes: Dict[str, Callable[[Dict[str, Any]], None]] = {
            "/api/curve-preview": self._handle_curve_preview,
            "/api/wedge/preview": self._handle_wedge_preview,
            "/api/wedge/tiff": self._handle_wedge_tiff,
            "/api/convert/preview": self._handle_convert_preview,
            "/api/convert/tiff": self._handle_convert_tiff,
        }

        handler = routes.get(parsed.path)
        if handler is None:
            self._send_json({"error": "Unknown endpoint"}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json_body()
            handler(payload)
        except PayloadTooLargeError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        except (ValueError, KeyError, TypeError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception:
            traceback.print_exc()
            self._send_json(
                {"error": "Internal server error while processing request."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )


def run_web_server(
    host: str = "0.0.0.0",
    port: Optional[int] = None,
    open_browser: bool = False,
) -> None:
    """Start the stateless digneg HTTP server."""
    actual_port = port or int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer((host, actual_port), DigNegRequestHandler)
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{display_host}:{actual_port}"
    print(f"digneg web interface listening on http://{host}:{actual_port} ({url})")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped digneg server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_web_server()
