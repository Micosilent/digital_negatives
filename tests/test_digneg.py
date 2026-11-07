"""Automated unit and integration tests for digneg."""

from __future__ import annotations

import base64
from http import HTTPStatus
from http.server import ThreadingHTTPServer
import io
import json
import threading
import unittest
from urllib.error import HTTPError
import urllib.request

import numpy as np
from PIL import Image

from digneg.io_utils import encode_tiff_16bit_bytes, load_image_normalized, to_luminance
from digneg.processor import downsample_luminance, process_image_to_negative
from digneg.profile import DEFAULT_PROFILE, CalibrationProfile, density_to_16bit_pixels
from digneg.web import DigNegRequestHandler
from digneg.wedge import generate_step_wedge_array


class TestIOUtils(unittest.TestCase):
    def test_16bit_grayscale_tiff_roundtrip(self) -> None:
        arr = np.linspace(0, 65535, 32 * 48, dtype=np.uint16).reshape(32, 48)
        raw = encode_tiff_16bit_bytes(arr, dpi=(600.0, 600.0))
        norm, dpi = load_image_normalized(raw)
        self.assertEqual(norm.shape, (32, 48))
        self.assertAlmostEqual(dpi[0], 600.0, places=1)
        np.testing.assert_allclose(norm, arr.astype(np.float64) / 65535.0, atol=1e-5)

    def test_16bit_rgb_tiff_roundtrip(self) -> None:
        arr = np.zeros((16, 24, 3), dtype=np.uint16)
        arr[:, :, 0] = 65535
        arr[:, :, 1] = 32768
        arr[:, :, 2] = 0
        raw = encode_tiff_16bit_bytes(arr, dpi=(300.0, 300.0))
        self.assertEqual(raw[:4], b"II*\x00")
        norm, dpi = load_image_normalized(raw)
        self.assertEqual(norm.shape, (16, 24, 3))
        self.assertAlmostEqual(dpi[0], 300.0, places=1)
        self.assertAlmostEqual(float(norm[0, 0, 0]), 1.0, places=4)
        self.assertAlmostEqual(float(norm[0, 0, 1]), 32768 / 65535.0, places=4)
        self.assertAlmostEqual(float(norm[0, 0, 2]), 0.0, places=4)


class TestProfileAndLUT(unittest.TestCase):
    def test_lut_matches_direct_evaluation(self) -> None:
        prof = DEFAULT_PROFILE
        lut = prof.build_16bit_lut()
        self.assertEqual(lut.shape, (65536, 3))
        self.assertEqual(lut.dtype, np.uint16)

        # Verify endpoints match direct float evaluation
        direct_endpoints = density_to_16bit_pixels(
            prof.evaluate_ink_density(np.array([0.0, 0.5, 1.0])),
            ink_mode=prof.ink_mode,
        )
        np.testing.assert_array_equal(lut[0], direct_endpoints[0])
        np.testing.assert_allclose(lut[32768], direct_endpoints[1], atol=1)
        np.testing.assert_array_equal(lut[65535], direct_endpoints[2])

    def test_profile_validation_bounds(self) -> None:
        with self.assertRaises(ValueError):
            CalibrationProfile(name="bad", steps_total=21, black_step=15.0, white_step=10.0)
        with self.assertRaises(ValueError):
            CalibrationProfile(name="bad_mid", steps_total=21, black_step=2.0, white_step=17.0, midtone_step=18.0)


class TestWedgeAndProcessor(unittest.TestCase):
    def test_wedge_dimension_bounds(self) -> None:
        with self.assertRaises(ValueError):
            generate_step_wedge_array(width_inches=500.0)
        with self.assertRaises(ValueError):
            generate_step_wedge_array(dpi=5000)

    def test_downsample_and_negative_inversion(self) -> None:
        lum = np.linspace(0.0, 1.0, 200 * 400, dtype=np.float64).reshape(200, 400)
        small = downsample_luminance(lum, max_dim=100)
        self.assertEqual(small.shape, (50, 100))

        prof = CalibrationProfile(
            name="mono",
            steps_total=21,
            black_step=2.0,   # 10% ink -> 90% pixel value (58982)
            white_step=18.0,  # 90% ink -> 10% pixel value (6554)
            ink_mode="grayscale",
            flip_horizontal=False,
        )
        neg = process_image_to_negative(np.array([[0.0, 1.0]]), profile=prof)
        self.assertEqual(neg.shape, (1, 2))
        self.assertGreater(neg[0, 0], neg[0, 1])  # Shadow (0.0) becomes bright negative pixel; highlight (1.0) becomes dark


class TestWebAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DigNegRequestHandler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_defaults_endpoint(self) -> None:
        res = json.loads(urllib.request.urlopen(f"{self.base_url}/api/defaults").read().decode())
        self.assertEqual(res["profiles"][0]["name"], "ilford_mg_rc_default")

    def test_static_traversal_blocked(self) -> None:
        with self.assertRaises(HTTPError) as ctx:
            urllib.request.urlopen(f"{self.base_url}/static/../web.py")
        self.assertEqual(ctx.exception.code, HTTPStatus.NOT_FOUND)

    def test_wedge_bounds_rejected_with_400(self) -> None:
        req = urllib.request.Request(
            f"{self.base_url}/api/wedge/preview",
            data=json.dumps({"steps": 21, "width_inches": 999.0, "dpi": 300}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    def test_convert_preview_and_tiff(self) -> None:
        img = Image.fromarray(np.linspace(0, 255, 48 * 64, dtype=np.uint8).reshape(48, 64))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        payload = {
            "filename": "sample.png",
            "image_base64": b64,
            "profile": DEFAULT_PROFILE.to_dict(),
        }
        req_prev = urllib.request.Request(
            f"{self.base_url}/api/convert/preview",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        prev_data = json.loads(urllib.request.urlopen(req_prev).read().decode())
        self.assertEqual(prev_data["width"], 64)
        self.assertEqual(prev_data["height"], 48)
        self.assertTrue(prev_data["negative_preview"].startswith("data:image/png;base64,"))

        req_tiff = urllib.request.Request(
            f"{self.base_url}/api/convert/tiff",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        tiff_bytes = urllib.request.urlopen(req_tiff).read()
        self.assertEqual(tiff_bytes[:4], b"II*\x00")


if __name__ == "__main__":
    unittest.main()
