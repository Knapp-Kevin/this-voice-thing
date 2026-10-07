import http.client
import json
import unittest

from this_voice_thing.integrations.local_api import LocalApiServer


class FakeBackend:
    def __init__(self):
        self.stream_request = None
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.write(b"buffered-audio")
        handle.close()
        self.buffered_path = handle.name

    def close(self):
        try:
            os.remove(self.buffered_path)
        except OSError:
            pass

    def health(self):
        return {"status": "ready"}

    def models(self):
        return []

    def voices(self):
        return []

    def synthesize(self, request):
        return {
            "path": self.buffered_path,
            "mime": "audio/mpeg",
            "temporary": False,
        }

    def synthesize_stream(self, request):
        self.stream_request = request
        return {
            "chunks": iter([b"\x01\x02", b"\x03\x04\x05\x06"]),
            "sample_rate": 48000,
            "channels": 1,
            "sample_format": "s16le",
            "streaming": "native",
            "watermark": "not-applied-live-path",
        }


class StreamingApiTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.server = LocalApiServer(self.backend, port=0, log=lambda _message: None)
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.backend.close()

    def request(self, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=3)
        connection.request(
            "POST",
            "/v1/audio/speech",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, body

    def test_buffered_speech_still_returns_content_length(self):
        status, headers, body = self.request({"input": "Buffered still works."})

        self.assertEqual(status, 200)
        self.assertEqual(body, b"buffered-audio")
        self.assertEqual(headers["Content-Length"], str(len(body)))
        self.assertNotIn("Transfer-Encoding", headers)

    def test_streams_pcm_with_chunked_transfer(self):
        status, headers, body = self.request({
            "input": "Hello from a stream.",
            "stream": True,
            "stream_format": "audio",
            "response_format": "pcm",
        })

        self.assertEqual(status, 200)
        self.assertEqual(body, b"\x01\x02\x03\x04\x05\x06")
        self.assertEqual(headers["Transfer-Encoding"], "chunked")
        self.assertEqual(headers["X-Audio-Sample-Rate"], "48000")
        self.assertEqual(headers["X-Audio-Sample-Format"], "s16le")
        self.assertEqual(headers["X-Streaming-Mode"], "native")
        self.assertEqual(headers["X-Audio-Watermark"], "not-applied-live-path")
        self.assertTrue(self.backend.stream_request["live"])

    def test_stream_requires_raw_audio_mode(self):
        status, _headers, body = self.request({
            "input": "Hello.",
            "stream": True,
            "response_format": "pcm",
        })
        self.assertEqual(status, 400)
        self.assertIn(b"stream_format", body)
        self.assertIsNone(self.backend.stream_request)

    def test_stream_rejects_non_pcm_output(self):
        status, _headers, body = self.request({
            "input": "Hello.",
            "stream": True,
            "stream_format": "audio",
            "response_format": "wav",
        })
        self.assertEqual(status, 400)
        self.assertIn(b"response_format", body)

    def test_stream_rejects_speed_processing(self):
        status, _headers, body = self.request({
            "input": "Hello.",
            "stream": True,
            "stream_format": "audio",
            "response_format": "pcm",
            "speed": 1.1,
        })
        self.assertEqual(status, 400)
        self.assertIn(b"speed", body)


if __name__ == "__main__":
    unittest.main()
