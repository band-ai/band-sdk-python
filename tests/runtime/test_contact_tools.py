"""Tests for ContactTools."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.contact_tools import ContactTools


@pytest.fixture
def mock_rest_client():
    """Mock AsyncRestClient for testing ContactTools."""
    client = MagicMock()
    client.agent_api_contacts = MagicMock()
    return client


@pytest.fixture
def contact_tools(mock_rest_client):
    """Create ContactTools instance with mocked client."""
    return ContactTools(mock_rest_client)


class TestListContacts:
    """Tests for list_contacts method."""

    async def test_list_contacts_returns_formatted_response(
        self, contact_tools, mock_rest_client
    ):
        """Verify list_contacts formats REST response correctly."""
        # Setup mock response
        contact = MagicMock()
        contact.id = "contact-123"
        contact.handle = "@alice"
        contact.name = "Alice"
        contact.type = "User"

        response = MagicMock()
        response.data = [contact]
        response.metadata = MagicMock()
        response.metadata.page = 1
        response.metadata.page_size = 50
        response.metadata.total_count = 1
        response.metadata.total_pages = 1
        mock_rest_client.agent_api_contacts.list_agent_contacts = AsyncMock(
            return_value=response
        )

        result = await contact_tools.list_contacts()

        assert len(result["contacts"]) == 1
        assert result["contacts"][0]["id"] == "contact-123"
        assert result["contacts"][0]["handle"] == "@alice"
        assert result["contacts"][0]["name"] == "Alice"
        assert result["contacts"][0]["type"] == "User"
        assert result["metadata"]["page"] == 1
        assert result["metadata"]["total_count"] == 1

    async def test_list_contacts_handles_empty_response(
        self, contact_tools, mock_rest_client
    ):
        """Verify empty list handling."""
        response = MagicMock()
        response.data = []
        response.metadata = MagicMock()
        response.metadata.page = 1
        response.metadata.page_size = 50
        response.metadata.total_count = 0
        response.metadata.total_pages = 0
        mock_rest_client.agent_api_contacts.list_agent_contacts = AsyncMock(
            return_value=response
        )

        result = await contact_tools.list_contacts()

        assert result["contacts"] == []
        assert result["metadata"]["total_count"] == 0

    async def test_list_contacts_pagination(self, contact_tools, mock_rest_client):
        """Verify page/page_size passed correctly."""
        response = MagicMock()
        response.data = []
        response.metadata = MagicMock()
        response.metadata.page = 2
        response.metadata.page_size = 25
        response.metadata.total_count = 30
        response.metadata.total_pages = 2
        mock_rest_client.agent_api_contacts.list_agent_contacts = AsyncMock(
            return_value=response
        )

        result = await contact_tools.list_contacts(page=2, page_size=25)

        mock_rest_client.agent_api_contacts.list_agent_contacts.assert_called_once_with(
            page=2, page_size=25
        )
        assert result["metadata"]["page"] == 2
        assert result["metadata"]["page_size"] == 25


class TestAddContact:
    """Tests for add_contact method."""

    async def test_add_contact_with_message(self, contact_tools, mock_rest_client):
        """Verify add_contact sends message."""
        response = MagicMock()
        response.data = MagicMock()
        response.data.id = "request-123"
        response.data.status = "pending"
        mock_rest_client.agent_api_contacts.add_agent_contact = AsyncMock(
            return_value=response
        )

        result = await contact_tools.add_contact("@alice", message="Let's collaborate!")

        assert result["id"] == "request-123"
        assert result["status"] == "pending"
        mock_rest_client.agent_api_contacts.add_agent_contact.assert_called_once_with(
            handle="@alice", message="Let's collaborate!"
        )

    async def test_add_contact_without_message(self, contact_tools, mock_rest_client):
        """Verify add_contact works without message."""
        response = MagicMock()
        response.data = MagicMock()
        response.data.id = "request-456"
        response.data.status = "pending"
        mock_rest_client.agent_api_contacts.add_agent_contact = AsyncMock(
            return_value=response
        )

        result = await contact_tools.add_contact("@bob")

        assert result["id"] == "request-456"
        mock_rest_client.agent_api_contacts.add_agent_contact.assert_called_once_with(
            handle="@bob", message=None
        )


class TestRemoveContact:
    """Tests for remove_contact method."""

    async def test_remove_contact_by_handle(self, contact_tools, mock_rest_client):
        """Verify remove by handle only passes handle (no contact_id=None)."""
        response = MagicMock()
        response.data = MagicMock()
        response.data.status = "removed"
        mock_rest_client.agent_api_contacts.remove_agent_contact = AsyncMock(
            return_value=response
        )

        result = await contact_tools.remove_contact(handle="@alice")

        assert result["status"] == "removed"
        # Only handle should be passed (contact_id omitted, not sent as null)
        mock_rest_client.agent_api_contacts.remove_agent_contact.assert_called_once_with(
            handle="@alice"
        )

    async def test_remove_contact_by_id(self, contact_tools, mock_rest_client):
        """Verify remove by contact_id only passes contact_id (no handle=None)."""
        response = MagicMock()
        response.data = MagicMock()
        response.data.status = "removed"
        mock_rest_client.agent_api_contacts.remove_agent_contact = AsyncMock(
            return_value=response
        )

        result = await contact_tools.remove_contact(contact_id="uuid-123")

        assert result["status"] == "removed"
        # Only contact_id should be passed (handle omitted, not sent as null)
        mock_rest_client.agent_api_contacts.remove_agent_contact.assert_called_once_with(
            contact_id="uuid-123"
        )

    async def test_remove_contact_requires_handle_or_id(self, contact_tools):
        """Verify validation - requires at least one identifier."""
        with pytest.raises(ValueError, match="Either handle or contact_id"):
            await contact_tools.remove_contact()


class TestListContactRequests:
    """Tests for list_contact_requests method."""

    async def test_list_contact_requests_received(
        self, contact_tools, mock_rest_client
    ):
        """Verify received requests returned."""
        received_req = MagicMock()
        received_req.id = "req-123"
        received_req.from_handle = "@charlie"
        received_req.from_name = "Charlie"
        received_req.message = "Hi!"
        received_req.status = "pending"
        received_req.inserted_at = "2024-01-01T00:00:00Z"

        response = MagicMock()
        response.data = MagicMock()
        response.data.received = [received_req]
        response.data.sent = []
        response.metadata = MagicMock()
        response.metadata.page = 1
        response.metadata.page_size = 50
        response.metadata.received = MagicMock()
        response.metadata.received.total = 1
        response.metadata.received.total_pages = 1
        response.metadata.sent = MagicMock()
        response.metadata.sent.total = 0
        response.metadata.sent.total_pages = 0
        mock_rest_client.agent_api_contacts.list_agent_contact_requests = AsyncMock(
            return_value=response
        )

        result = await contact_tools.list_contact_requests()

        assert len(result["received"]) == 1
        assert result["received"][0]["from_handle"] == "@charlie"
        assert result["received"][0]["status"] == "pending"
        assert result["metadata"]["received"]["total"] == 1

    async def test_list_contact_requests_sent(self, contact_tools, mock_rest_client):
        """Verify sent requests returned."""
        sent_req = MagicMock()
        sent_req.id = "req-456"
        sent_req.to_handle = "@diana"
        sent_req.to_name = "Diana"
        sent_req.message = "Please connect"
        sent_req.status = "pending"
        sent_req.inserted_at = "2024-01-02T00:00:00Z"

        response = MagicMock()
        response.data = MagicMock()
        response.data.received = []
        response.data.sent = [sent_req]
        response.metadata = MagicMock()
        response.metadata.page = 1
        response.metadata.page_size = 50
        response.metadata.received = MagicMock()
        response.metadata.received.total = 0
        response.metadata.received.total_pages = 0
        response.metadata.sent = MagicMock()
        response.metadata.sent.total = 1
        response.metadata.sent.total_pages = 1
        mock_rest_client.agent_api_contacts.list_agent_contact_requests = AsyncMock(
            return_value=response
        )

        result = await contact_tools.list_contact_requests()

        assert len(result["sent"]) == 1
        assert result["sent"][0]["to_handle"] == "@diana"
        assert result["metadata"]["sent"]["total"] == 1

    async def test_list_contact_requests_with_status_filter(
        self, contact_tools, mock_rest_client
    ):
        """Verify sent_status filter."""
        response = MagicMock()
        response.data = MagicMock()
        response.data.received = []
        response.data.sent = []
        response.metadata = MagicMock()
        response.metadata.page = 1
        response.metadata.page_size = 50
        response.metadata.received = MagicMock()
        response.metadata.received.total = 0
        response.metadata.received.total_pages = 0
        response.metadata.sent = MagicMock()
        response.metadata.sent.total = 0
        response.metadata.sent.total_pages = 0
        mock_rest_client.agent_api_contacts.list_agent_contact_requests = AsyncMock(
            return_value=response
        )

        await contact_tools.list_contact_requests(sent_status="approved")

        mock_rest_client.agent_api_contacts.list_agent_contact_requests.assert_called_once_with(
            page=1, page_size=50, sent_status="approved"
        )


class TestRespondContactRequest:
    """Tests for respond_contact_request method."""

    async def test_respond_contact_request_approve(
        self, contact_tools, mock_rest_client
    ):
        """Verify approve action."""
        response = MagicMock()
        response.data = MagicMock()
        response.data.id = "req-123"
        response.data.status = "approved"
        mock_rest_client.agent_api_contacts.respond_to_agent_contact_request = (
            AsyncMock(return_value=response)
        )

        result = await contact_tools.respond_contact_request(
            "approve", handle="@charlie"
        )

        assert result["status"] == "approved"

    async def test_respond_contact_request_reject(
        self, contact_tools, mock_rest_client
    ):
        """Verify reject action."""
        response = MagicMock()
        response.data = MagicMock()
        response.data.id = "req-123"
        response.data.status = "rejected"
        mock_rest_client.agent_api_contacts.respond_to_agent_contact_request = (
            AsyncMock(return_value=response)
        )

        result = await contact_tools.respond_contact_request(
            "reject", handle="@charlie"
        )

        assert result["status"] == "rejected"

    async def test_respond_contact_request_cancel(
        self, contact_tools, mock_rest_client
    ):
        """Verify cancel action."""
        response = MagicMock()
        response.data = MagicMock()
        response.data.id = "req-456"
        response.data.status = "cancelled"
        mock_rest_client.agent_api_contacts.respond_to_agent_contact_request = (
            AsyncMock(return_value=response)
        )

        result = await contact_tools.respond_contact_request("cancel", handle="@diana")

        assert result["status"] == "cancelled"

    async def test_respond_contact_request_by_handle(
        self, contact_tools, mock_rest_client
    ):
        """Verify by handle."""
        response = MagicMock()
        response.data = MagicMock()
        response.data.id = "req-123"
        response.data.status = "approved"
        mock_rest_client.agent_api_contacts.respond_to_agent_contact_request = (
            AsyncMock(return_value=response)
        )

        await contact_tools.respond_contact_request("approve", handle="@alice")

        mock_rest_client.agent_api_contacts.respond_to_agent_contact_request.assert_called_once_with(
            action="approve", handle="@alice"
        )

    async def test_respond_contact_request_by_id(self, contact_tools, mock_rest_client):
        """Verify by request_id."""
        response = MagicMock()
        response.data = MagicMock()
        response.data.id = "req-789"
        response.data.status = "approved"
        mock_rest_client.agent_api_contacts.respond_to_agent_contact_request = (
            AsyncMock(return_value=response)
        )

        await contact_tools.respond_contact_request("approve", request_id="req-789")

        mock_rest_client.agent_api_contacts.respond_to_agent_contact_request.assert_called_once_with(
            action="approve", request_id="req-789"
        )

    async def test_respond_contact_request_requires_handle_or_id(self, contact_tools):
        """Verify validation - requires at least one identifier."""
        with pytest.raises(ValueError, match="Either handle or request_id"):
            await contact_tools.respond_contact_request("approve")
