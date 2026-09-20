from fastapi.testclient import TestClient

from apps.api.main import app


def test_demo_login_issues_scoped_token_and_customer_cannot_read_approvals() -> None:
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/demo-login",
            json={"principal_id": "usr_demo_0001", "role": "customer"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        denied = client.get(
            "/api/v1/approvals",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "insufficient scope"


def test_invalid_bearer_token_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/approvals",
            headers={"Authorization": "Bearer tampered-token"},
        )
    assert response.status_code == 401
