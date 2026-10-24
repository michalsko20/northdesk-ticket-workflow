"""Tests for security hardening: settings validation, input sanitization, headers."""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from northdesk.api import ApprovalRequest, TicketRequest, create_app
from northdesk.domain.models import Ticket
from northdesk.domain.payloads import has_control_characters
from northdesk.domain.rules import validate_decision
from northdesk.settings import Settings


def _client() -> AsyncClient:
    app = create_app()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestHasControlCharacters:
    def test_accepts_printable_text(self):
        assert not has_control_characters("Hello world! Café 日本語 \U0001f389")

    def test_accepts_newline_and_tab(self):
        assert not has_control_characters("line one\nline two\ttabbed")

    def test_rejects_null_byte(self):
        assert has_control_characters("hello\x00world")

    def test_rejects_bel_and_escape(self):
        assert has_control_characters("hello\x07bell")
        assert has_control_characters("hello\x1b[31mcolor")

    def test_rejects_bidi_override(self):
        assert has_control_characters("user‮evil")

    def test_rejects_zero_width(self):
        assert has_control_characters("zero​width")

    def test_rejects_carriage_return(self):
        assert has_control_characters("bad\r\nline")

    def test_rejects_del(self):
        assert has_control_characters("bad\x7fchar")


class TestTicketRequestSanitization:
    def test_rejects_null_byte_in_subject(self):
        with pytest.raises(ValidationError):
            TicketRequest(
                ticket_id="T-SEC-1", customer_id="C-100", subject="null\x00byte", body="normal"
            )

    def test_rejects_escape_sequence_in_body(self):
        with pytest.raises(ValidationError):
            TicketRequest(
                ticket_id="T-SEC-2", customer_id="C-100", subject="normal", body="inj\x1b[31mect"
            )

    def test_rejects_bidi_in_reviewer(self):
        with pytest.raises(ValidationError):
            ApprovalRequest(approved=True, reviewer="admin‮spoof")

    def test_rejects_carriage_return_in_reviewer(self):
        with pytest.raises(ValidationError):
            ApprovalRequest(approved=True, reviewer="admin\r\ninjected-header")

    def test_accepts_clean_input(self):
        request = TicketRequest(
            ticket_id="T-SEC-4",
            customer_id="C-100",
            subject="Password reset",
            body="I need help resetting my password.\nSecond line.",
        )
        assert request.subject == "Password reset"

    def test_accepts_clean_reviewer(self):
        request = ApprovalRequest(approved=True, reviewer="security-team-lead")
        assert request.reviewer == "security-team-lead"


class TestSettingsValidation:
    def test_rejects_non_https_glm_url(self):
        with pytest.raises(ValidationError, match="HTTPS"):
            Settings(llm_mode="glm", zai_api_key="key", glm_base_url="http://api.example.com/v4")

    def test_accepts_https_glm_url(self):
        settings = Settings(
            llm_mode="glm",
            zai_api_key="key",
            glm_base_url="https://api.z.ai/v4/chat/completions",
        )
        assert settings.glm_base_url.startswith("https://")

    def test_accepts_local_http_glm_url(self):
        settings = Settings(llm_mode="glm", zai_api_key="key", glm_base_url="http://localhost:8080")
        assert settings.glm_base_url.startswith("http://localhost")

    def test_accepts_loopback_http_glm_url(self):
        settings = Settings(
            llm_mode="glm", zai_api_key="key", glm_base_url="http://127.0.0.1:8080/v1"
        )
        assert settings.glm_base_url.startswith("http://127.0.0.1")

    def test_rejects_traversal_db_path(self):
        with pytest.raises(ValidationError, match="traverse"):
            Settings(helpdesk_db_path="../../etc/shadow")

    def test_rejects_null_byte_db_path(self):
        with pytest.raises(ValidationError, match="null"):
            Settings(helpdesk_db_path="db\x00.sqlite")

    def test_rejects_invalid_temporal_address(self):
        with pytest.raises(ValidationError, match="host:port"):
            Settings(temporal_address="not-a-valid-address")

    def test_rejects_out_of_range_port(self):
        with pytest.raises(ValidationError, match="port"):
            Settings(temporal_address="localhost:99999")

    def test_accepts_valid_defaults(self):
        settings = Settings(temporal_address="localhost:7233", helpdesk_db_path="test.db")
        assert settings.temporal_address == "localhost:7233"


class TestSecurityHeaders:
    @pytest.mark.asyncio
    async def test_health_has_security_headers(self):
        async with _client() as client:
            response = await client.get("/health/live")
            assert response.status_code == 200
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["cache-control"] == "no-store"

    @pytest.mark.asyncio
    async def test_validation_error_has_security_headers(self):
        async with _client() as client:
            response = await client.post("/tickets", json={})
            assert response.status_code == 422
            assert response.headers["x-content-type-options"] == "nosniff"


class TestPolicyControlCharacterRejection:
    def _ticket(self) -> Ticket:
        return Ticket("T-POL", "C-100", "Test", "Test body")

    def test_reply_rejects_control_characters_in_message(self):
        decision = validate_decision(
            self._ticket(),
            {
                "category": "account",
                "priority": "normal",
                "action": "reply",
                "message": "help\x00inject",
                "articles": [],
            },
            invoices=[],
            article_ids=set(),
        )
        assert decision.action == "escalate"
        assert decision.reason == "invalid_reply_control_characters"

    def test_close_rejects_control_characters_in_reason(self):
        decision = validate_decision(
            self._ticket(),
            {
                "category": "other",
                "priority": "low",
                "action": "close",
                "reason": "done\x1b[0m",
            },
            invoices=[],
            article_ids=set(),
        )
        assert decision.action == "escalate"
        assert decision.reason == "invalid_reason_control_characters"

    def test_reply_accepts_clean_message(self):
        decision = validate_decision(
            self._ticket(),
            {
                "category": "account",
                "priority": "normal",
                "action": "reply",
                "message": "Here is the reset link.",
                "articles": [],
            },
            invoices=[],
            article_ids=set(),
        )
        assert decision.action == "reply"
        assert decision.message == "Here is the reset link."


class TestHttpSmugglingDefense:
    """Test HTTP middleware defense against request smuggling vectors."""

    @staticmethod
    def _make_scope(headers: list[tuple[bytes, bytes]], path: bytes = b"/tickets") -> dict:
        return {
            "type": "http",
            "method": "POST",
            "path": path.decode(),
            "headers": headers,
        }

    @pytest.mark.asyncio
    async def test_rejects_conflicting_content_length(self):
        from northdesk.http_limits import HttpLimits

        scope = self._make_scope([(b"content-length", b"10"), (b"content-length", b"20")])
        responses = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            responses.append(message)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = HttpLimits(app, max_body_bytes=1024, max_inflight=10, body_timeout=5)
        await middleware(scope, receive, send)
        assert responses[0]["status"] == 400

    @pytest.mark.asyncio
    async def test_rejects_transfer_encoding_and_content_length(self):
        from northdesk.http_limits import HttpLimits

        scope = self._make_scope([(b"transfer-encoding", b"chunked"), (b"content-length", b"10")])
        responses = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            responses.append(message)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = HttpLimits(app, max_body_bytes=1024, max_inflight=10, body_timeout=5)
        await middleware(scope, receive, send)
        assert responses[0]["status"] == 400

    @pytest.mark.asyncio
    async def test_allows_single_content_length(self):
        from northdesk.http_limits import HttpLimits

        scope = self._make_scope([(b"content-length", b"5")])
        responses = []

        async def receive():
            return {"type": "http.request", "body": b"hello", "more_body": False}

        async def send(message):
            responses.append(message)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = HttpLimits(app, max_body_bytes=1024, max_inflight=10, body_timeout=5)
        await middleware(scope, receive, send)
        assert responses[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_allows_duplicate_identical_content_length(self):
        from northdesk.http_limits import HttpLimits

        scope = self._make_scope([(b"content-length", b"5"), (b"content-length", b"5")])
        responses = []

        async def receive():
            return {"type": "http.request", "body": b"hello", "more_body": False}

        async def send(message):
            responses.append(message)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = HttpLimits(app, max_body_bytes=1024, max_inflight=10, body_timeout=5)
        await middleware(scope, receive, send)
        assert responses[0]["status"] == 200


class TestHelpdeskPermissions:
    def test_db_file_has_restrictive_permissions(self, tmp_path):
        import os
        import stat

        from northdesk.services.helpdesk import FakeHelpdesk

        db_path = str(tmp_path / "test.db")
        FakeHelpdesk(db_path)
        mode = stat.S_IMODE(os.stat(db_path).st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_state_directory_has_restrictive_permissions(self, tmp_path):
        import os
        import stat

        from northdesk.services.helpdesk import FakeHelpdesk

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        db_path = str(state_dir / "test.db")
        FakeHelpdesk(db_path)
        mode = stat.S_IMODE(os.stat(str(state_dir)).st_mode)
        assert mode == 0o700, f"Expected 0o700, got {oct(mode)}"


class TestSecretLeakagePrevention:
    """Verify that sensitive settings are not exposed through error messages."""

    def test_settings_str_does_not_leak_api_key(self):
        settings = Settings(llm_mode="glm", zai_api_key="super-secret-key-12345")
        rendered = str(settings)
        assert "super-secret-key-12345" not in rendered

    def test_settings_repr_does_not_leak_api_key(self):
        settings = Settings(llm_mode="glm", zai_api_key="super-secret-key-12345")
        rendered = repr(settings)
        assert "super-secret-key-12345" not in rendered

    def test_validation_error_does_not_leak_api_key(self):
        with pytest.raises(ValidationError) as exc_info:
            Settings(
                llm_mode="glm",
                zai_api_key="",
                glm_base_url="http://untrusted.example.com/v1",
            )
        message = str(exc_info.value)
        assert "super-secret" not in message

    def test_validation_error_does_not_leak_base_url(self):
        with pytest.raises(ValidationError) as exc_info:
            Settings(
                llm_mode="glm",
                zai_api_key="key",
                glm_base_url="http://internal-infra.svc.local:8080/v1",
            )
        message = str(exc_info.value)
        assert "internal-infra.svc.local" not in message


class TestToolInputSanitization:
    def test_search_kb_rejects_control_characters_in_query(self):
        from northdesk.agent.providers import PermanentAgentError
        from northdesk.agent.tools import execute_tool
        from northdesk.services.catalog import Catalog

        catalog = Catalog()
        ticket = Ticket("T-TOOL", "C-100", "Test", "Test body")
        with pytest.raises(PermanentAgentError, match="unknown_tool_or_invalid_arguments"):
            execute_tool(catalog, ticket, "search_kb", '{"query": "help\\u0000me"}')

    def test_search_kb_accepts_clean_query(self):
        from northdesk.agent.tools import execute_tool
        from northdesk.services.catalog import Catalog

        catalog = Catalog()
        ticket = Ticket("T-TOOL", "C-100", "Test", "Test body")
        result = execute_tool(catalog, ticket, "search_kb", '{"query": "reset password"}')
        assert isinstance(result, list)

    def test_get_customer_rejects_non_empty_params(self):
        from northdesk.agent.providers import PermanentAgentError
        from northdesk.agent.tools import execute_tool
        from northdesk.services.catalog import Catalog

        catalog = Catalog()
        ticket = Ticket("T-TOOL", "C-100", "Test", "Test body")
        with pytest.raises(PermanentAgentError, match="unknown_tool_or_invalid_arguments"):
            execute_tool(catalog, ticket, "get_customer", '{"customer_id": "other"}')


class TestTracingConfiguration:
    def test_tracing_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        from northdesk.tracing import _enabled, configure_tracing

        assert not _enabled()
        assert configure_tracing("test") is None

    def test_tracing_enabled_with_false(self, monkeypatch):
        monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
        from northdesk.tracing import _enabled

        assert _enabled()

    def test_tracing_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://custom:9999")
        from northdesk.tracing import configure_tracing

        provider = configure_tracing("test-role")
        assert provider is not None
        # Clean up to avoid leaking spans into other tests.
        provider.shutdown()
