from __future__ import annotations

import base64
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from light_image_editor.image_model_api import (
    ImageTextEditApiRequest,
    check_image_api_connection,
    edit_image_text,
)


def _png_bytes(color: str = "#336699") -> bytes:
    image = Image.new("RGB", (32, 24), color)
    try:
        buffer = BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()
    finally:
        image.close()


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self, amount: int = -1) -> bytes:
        return self.payload if amount < 0 else self.payload[:amount]

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False


class ImageModelApiTests(unittest.TestCase):
    def test_connection_check_uses_models_endpoint_without_uploading_image(self) -> None:
        captured: dict[str, Any] = {}

        def opener(request: Any, *, timeout: float) -> _FakeResponse:
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse(b'{"object":"list","data":[]}')

        message = check_image_api_connection(
            "https://api.openai.com/v1/images/edits",
            "secret",
            opener=opener,
        )

        request = captured["request"]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/models")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertIsNone(request.data)
        self.assertIn("连接成功", message)

    def test_request_builds_style_preserving_prompt_and_hides_key_from_repr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(_png_bytes())
            request = ImageTextEditApiRequest(
                source_path=source,
                endpoint="https://example.com/v1/images/edits",
                model="image-edit-model",
                api_key="super-secret-key",
                original_text="夏日特惠",
                replacement_text="秋日上新",
                instruction="只改右上角标题",
            )

            self.assertNotIn("super-secret-key", repr(request))
            self.assertIn("夏日特惠", request.prompt)
            self.assertIn("秋日上新", request.prompt)
            self.assertIn("字体风格", request.prompt)
            self.assertIn("透视", request.prompt)
            self.assertIn("其他人物、物体、背景和构图保持不变", request.prompt)

    def test_remote_endpoint_requires_key_but_local_endpoint_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(_png_bytes())
            with self.assertRaisesRegex(ValueError, "API Key"):
                ImageTextEditApiRequest(
                    source_path=source,
                    endpoint="https://example.com/v1/images/edits",
                    model="image-edit-model",
                    api_key="",
                    replacement_text="新文字",
                )
            request = ImageTextEditApiRequest(
                source_path=source,
                endpoint="http://127.0.0.1:9000/v1/images/edits",
                model="local-model",
                api_key="",
                replacement_text="新文字",
            )
            self.assertEqual(request.model, "local-model")

    def test_edit_api_sends_multipart_image_and_decodes_base64_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(_png_bytes("#112233"))
            result_png = _png_bytes("#cc3344")
            response = json.dumps(
                {"data": [{"b64_json": base64.b64encode(result_png).decode("ascii")}]}
            ).encode("utf-8")
            captured: dict[str, Any] = {}

            def opener(request: Any, *, timeout: float) -> _FakeResponse:
                captured["request"] = request
                captured["timeout"] = timeout
                return _FakeResponse(response)

            request = ImageTextEditApiRequest(
                source_path=source,
                endpoint="https://example.com/v1/images/edits",
                model="image-edit-model",
                api_key="secret",
                original_text="旧文字",
                replacement_text="新文字",
                quality="high",
            )
            result = edit_image_text(request, opener=opener)

            http_request = captured["request"]
            body = bytes(http_request.data)
            self.assertEqual(http_request.get_header("Authorization"), "Bearer secret")
            self.assertIn(b'name="model"', body)
            self.assertIn(b"image-edit-model", body)
            self.assertIn(b'name="image"', body)
            self.assertIn("旧文字".encode(), body)
            self.assertIn("新文字".encode(), body)
            with Image.open(BytesIO(result.png_bytes)) as image:
                self.assertEqual(image.size, (32, 24))
                self.assertEqual(image.convert("RGB").getpixel((0, 0)), (204, 51, 68))

    def test_adaptive_reverse_model_omits_unsupported_fields_and_accepts_data_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(_png_bytes("#112233"))
            generated = Image.new("RGB", (40, 40), "#33cc44")
            generated_buffer = BytesIO()
            generated.save(generated_buffer, "PNG")
            generated.close()
            result_png = generated_buffer.getvalue()
            encoded = base64.b64encode(result_png).decode("ascii")
            response = json.dumps(
                {"data": [{"b64_json": f"data:image/png;base64,{encoded}"}]}
            ).encode("utf-8")
            captured: dict[str, Any] = {}

            def opener(request: Any, *, timeout: float) -> _FakeResponse:
                captured["request"] = request
                captured["timeout"] = timeout
                return _FakeResponse(response)

            request = ImageTextEditApiRequest(
                source_path=source,
                endpoint="https://example.com/v1/images/edits",
                model="gpt-image-2-all",
                api_key="secret",
                replacement_text="新文字",
                quality="high",
            )
            result = edit_image_text(request, opener=opener)

            body = bytes(captured["request"].data)
            self.assertIn(b"gpt-image-2-all", body)
            self.assertIn(b"response_format", body)
            self.assertNotIn(b'name="size"', body)
            self.assertNotIn(b'name="quality"', body)
            self.assertNotIn(b'name="output_format"', body)
            with Image.open(BytesIO(result.png_bytes)) as image:
                self.assertEqual(image.size, (32, 24))
                self.assertEqual(image.convert("RGB").getpixel((0, 0)), (51, 204, 68))


if __name__ == "__main__":
    unittest.main()
