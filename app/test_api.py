"""
FastAPI endpoint tests for the LexiAgent API server.

These tests avoid Ollama/model dependencies by exercising static endpoints
directly and mocking the extraction pipeline when conversation state is needed.

Usage:
    python app/test_api.py
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import api_server


class LexiAgentAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api_server.app)

    def setUp(self):
        api_server._SESSION_STATES.clear()

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_models_endpoint_lists_lexiagent(self):
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        model_ids = [item["id"] for item in payload["data"]]
        self.assertIn("lexiagent", model_ids)

    def test_empty_messages_returns_welcome_text(self):
        response = self.client.post(
            "/v1/chat/completions",
            json={"messages": [], "stream": False},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["object"], "chat.completion")
        content = payload["choices"][0]["message"]["content"]
        self.assertIn("Welcome to **LexiAgent**", content)

    def test_unknown_contract_type_prompts_for_selection(self):
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "user", "content": "I need help writing a merger memo."}
                ],
                "stream": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        content = response.json()["choices"][0]["message"]["content"]
        self.assertIn("I couldn't determine the contract type", content)
        self.assertIn("NDA", content)

    def test_streaming_response_uses_sse_and_ends_with_done(self):
        response = self.client.post(
            "/v1/chat/completions",
            json={"messages": [], "stream": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers.get("content-type", ""))
        body = response.text
        self.assertIn('"object": "chat.completion.chunk"', body)
        self.assertTrue(body.endswith("data: [DONE]\n\n"))

    def test_non_streaming_response_is_openai_compatible(self):
        response = self.client.post(
            "/v1/chat/completions",
            json={"messages": [], "stream": False},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["object"], "chat.completion")
        self.assertEqual(payload["choices"][0]["message"]["role"], "assistant")
        self.assertIn("usage", payload)

    @unittest.skip("LEXIAGENT_API_KEY auth is not implemented in api_server.py yet")
    def test_api_key_auth_rejects_missing_bearer_token(self):
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 401)

    def test_download_nonexistent_docx_returns_404(self):
        response = self.client.get("/download/does-not-exist.docx")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "File not found")

    def test_different_first_messages_create_independent_sessions(self):
        mocked_extraction = {
            "known_answers": {"party_a_name": "Acme Corp"},
            "field_evidence": {"party_a_name": "Acme Corp"},
            "follow_up_questions": [
                {"field": "party_b_name", "question": "Who is Party B?"}
            ],
        }
        mocked_verified = (
            {"party_a_name": "Acme Corp"},
            [{"field": "party_b_name", "question": "Who is Party B?"}],
            {"party_a_name": "Acme Corp"},
        )

        with patch.object(api_server, "extract_answers_from_prompt", return_value=mocked_extraction), patch.object(
            api_server, "verify_and_prepare", return_value=mocked_verified
        ):
            response_a = self.client.post(
                "/v1/chat/completions",
                json={
                    "messages": [
                        {"role": "user", "content": "Please draft an NDA for Acme and Beta."}
                    ],
                    "stream": False,
                },
            )
            response_b = self.client.post(
                "/v1/chat/completions",
                json={
                    "messages": [
                        {
                            "role": "user",
                            "content": "Please draft a consulting agreement for Orbit Labs and Nova Advisors.",
                        }
                    ],
                    "stream": False,
                },
            )

        self.assertEqual(response_a.status_code, 200)
        self.assertEqual(response_b.status_code, 200)
        self.assertEqual(len(api_server._SESSION_STATES), 2)
        states = list(api_server._SESSION_STATES.values())
        self.assertNotEqual(states[0]["initial_prompt"], states[1]["initial_prompt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
