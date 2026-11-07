"""Stateless HTTP server and API routes for the digneg web interface."""

from __future__ import annotations

import base64
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import tempfile
import traceback
from typing import Any, Dict, Optional
from urllib.parse import urlparse
import webbrowser

import numpy as np

from digneg.io_utils import load_image_normalized, save_tiff_16bit, to_luminance
from digneg.processor import array_16bit_to_8bit_preview_pil, process_image_to_negative
from digneg.profile import INK_MODES, CalibrationProfile
from digneg.wedge import generate_step_wedge_array


STATIC_DIR = Path(__file__).resolve().parent / "static"

DEFAULT_STARTER_PROFILES = [
    CalibrationProfile(
        name="ilford_mg_rc_default",
        steps_total=21,
        black_step=2.0,
        white_step=17.0,
        midtone_step=9.5,
        gamma=1.0,
        ink_mode="pyro_amber",
        flip_horizontal=True,
        notes="Ilford Multigrade RC, Filter #2, 21-step calibration baseline.",
    ).to_dict(),
]


def _pil_to_base64_png(pil_img) -> str:
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


class DigNegRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler serving static UI assets and stateless JSON/TIFF API endpoints."""

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
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(raw)

    def _serve_static_file(self, rel_path: str) -> None:
        target = (STATIC_DIR / rel_path).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return
        mime, _ = mimetypes.guess_type(str(target))
        content_type = mime or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type = f"{content_type}; charset=utf-8"
        self._send_bytes(target.read_bytes(), content_type)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(body.decode("utf-8"))

    def _parse_profile(self, payload: Dict[str, Any]) -> Optional[CalibrationProfile]:
        prof_data = payload.get("profile")
        if isinstance(prof_data, dict):
            return CalibrationProfile.from_dict(prof_data)
        return None

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
            self._send_json({"profiles": DEFAULT_STARTER_PROFILES, "ink_modes": INK_MODES})
            return

        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()

            if parsed.path == "/api/curve-preview":
                prof = CalibrationProfile.from_dict(payload)
                self._send_json({"curve": prof.build_curve_preview_data()})
                return

            if parsed.path in ("/api/wedge/preview", "/api/wedge/tiff"):
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

                if parsed.path == "/api/wedge/preview":
                    pil_prev = array_16bit_to_8bit_preview_pil(arr_16, max_dim=1100)
                    self._send_json(
                        {
                            "preview": _pil_to_base64_png(pil_prev),
                            "width": int(arr_16.shape[1]),
                            "height": int(arr_16.shape[0]),
                        }
                    )
                else:
                    with tempfile.NamedTemporaryFile(suffix=".tiff") as tmp:
                        save_tiff_16bit(tmp.name, arr_16, dpi=(float(dpi), float(dpi)))
                        tiff_bytes = Path(tmp.name).read_bytes()
                    self._send_bytes(
                        tiff_bytes,
                        content_type="image/tiff",
                        filename=f"wedge_{steps}step_{ink_mode}_16bit.tiff",
                    )
                return

            if parsed.path in ("/api/convert/preview", "/api/convert/tiff"):
                b64_data = payload.get("image_base64")
                if not b64_data:
                    self._send_json({"error": "Missing image_base64"}, status=HTTPStatus.BAD_REQUEST)
                    return

                raw_bytes = base64.b64decode(b64_data)
                suffix = Path(payload.get("filename", "upload.png")).suffix or ".png"

                with tempfile.NamedTemporaryFile(suffix=suffix) as tmp_in:
                    Path(tmp_in.name).write_bytes(raw_bytes)
                    arr_norm, orig_dpi = load_image_normalized(tmp_in.name)

                prof = self._parse_profile(payload) or CalibrationProfile(name="default_linear")
                if payload.get("override_ink_mode"):
                    prof.ink_mode = str(payload["override_ink_mode"])
                override_flip = payload.get("override_flip")
                dpi_val = float(payload["override_dpi"]) if payload.get("override_dpi") else orig_dpi[0]

                neg_16 = process_image_to_negative(
                    arr_norm,
                    profile=prof,
                    override_flip=override_flip,
                )

                if parsed.path == "/api/convert/preview":
                    lum = to_luminance(arr_norm)
                    pos_16 = np.rint(lum * 65535.0).astype(np.uint16)
                    pos_pil = array_16bit_to_8bit_preview_pil(pos_16, max_dim=720)
                    neg_pil = array_16bit_to_8bit_preview_pil(neg_16, max_dim=720)
                    self._send_json(
                        {
                            "positive_preview": _pil_to_base64_png(pos_pil),
                            "negative_preview": _pil_to_base64_png(neg_pil),
                            "width": int(neg_16.shape[1]),
                            "height": int(neg_16.shape[0]),
                            "dpi": int(round(dpi_val)),
                            "ink_mode": prof.ink_mode,
                        }
                    )
                else:
                    with tempfile.NamedTemporaryFile(suffix=".tiff") as tmp_out:
                        save_tiff_16bit(tmp_out.name, neg_16, dpi=(dpi_val, dpi_val))
                        tiff_bytes = Path(tmp_out.name).read_bytes()
                    self._send_bytes(
                        tiff_bytes,
                        content_type="image/tiff",
                        filename="digital_negative_16bit.tiff",
                    )
                return

            self._send_json({"error": "Unknown endpoint"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


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
