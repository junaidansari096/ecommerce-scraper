import os
import sys
import pytest
import httpx

# Ensure app path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Set Testing environment for the test run
os.environ["APP_ENV"] = "testing"

from server import app
from auth_manager import AuthManager, create_jwt_token, decode_jwt_token

@pytest.mark.anyio
async def test_health_and_ping_endpoint():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/ping")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "online"
        assert data["service"] == "storehub-platform"
        assert data["is_testing"] is True
        assert data["environment"] == "testing"

@pytest.mark.anyio
async def test_network_info_testing_environment():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/network-info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_testing"] is True
        assert data["app_env"] == "testing"
        assert "user_data_test" in data["storage_root"]
        assert "Local Testing Sandbox" in data["environment"] or "Render Cloud" in data["environment"]

@pytest.mark.anyio
async def test_user_registration_and_login_in_testing():
    import uuid
    auth = AuthManager()
    assert auth.is_testing_mode() is True

    test_email = f"tester_{uuid.uuid4().hex[:6]}@storehub.io"
    test_pass = "TestSecret123!"
    test_name = "Sandbox Tester"

    # Register in testing sandbox
    ok, msg, user, token = auth.register_user(test_name, test_email, test_pass)
    assert ok is True
    assert user["email"] == test_email
    assert token is not None

    # Verify JWT decoding
    payload = decode_jwt_token(token)
    assert payload is not None
    assert payload["sub"] == user["id"]
    assert payload["email"] == test_email

    # Authenticate
    ok_auth, msg_auth, user_auth, token_auth = auth.authenticate_user(test_email, test_pass)
    assert ok_auth is True
    assert user_auth["id"] == user["id"]

@pytest.mark.anyio
async def test_project_creation_and_shopify_template():
    import uuid
    auth = AuthManager()
    user = auth.get_user_by_id("usr_customer_1")
    if not user:
        ok, _, user, _ = auth.register_user("Apex Retailers", "customer1@storehub.io", "Password123!", user_id="usr_customer_1")
    
    user_db = auth.get_user_db(user["id"])
    proj_id = f"proj_test_{uuid.uuid4().hex[:8]}"
    p = user_db.create_project(proj_id, "Test Sandbox Catalog", "amazon", {})
    assert p["id"] == proj_id

    # Apply Shopify 61-column template
    cols = user_db.apply_shopify_schema(proj_id)
    assert len(cols) == 61
    assert "Title" in cols
    assert "URL handle" in cols
    assert "Product image URL" in cols
    assert "Image position" in cols
    assert "Price" in cols
