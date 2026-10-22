"""工单 01：账号与私有问卷草稿。"""


def test_new_user_can_register(client):
    response = client.post(
        "/api/register",
        json={"username": "zhang", "password": "secret123"},
    )

    assert response.status_code == 201


def test_duplicate_username_is_rejected(client):
    """同用户名再次注册被拒绝，且不暴露内部细节。"""
    client.post("/api/register", json={"username": "zhang", "password": "secret123"})

    response = client.post(
        "/api/register", json={"username": "zhang", "password": "another456"}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "用户名已被占用"


def test_registered_user_can_login(client):
    """注册后可用正确密码登录，并拿到 HttpOnly 会话 Cookie。"""
    client.post("/api/register", json={"username": "zhang", "password": "secret123"})

    response = client.post(
        "/api/login", json={"username": "zhang", "password": "secret123"}
    )

    assert response.status_code == 200
    assert response.json()["username"] == "zhang"
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie


def test_wrong_password_is_rejected_without_leaking_details(client):
    """错误密码被拒绝；错误信息为通用文案，不含内部细节。"""
    client.post("/api/register", json={"username": "zhang", "password": "secret123"})

    response = client.post(
        "/api/login", json={"username": "zhang", "password": "wrong-pass"}
    )

    assert response.status_code == 401
    body = response.text.lower()
    assert "pbkdf2" not in body
    assert "secret123" not in body
    assert response.json()["detail"] == "用户名或密码错误"


def test_unknown_username_gets_same_generic_message(client):
    """不存在的用户名与错误密码返回同样文案，避免用户枚举。"""
    response = client.post(
        "/api/login", json={"username": "nobody", "password": "whatever"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"


def test_logout_invalidates_session(client):
    """退出后原会话不再可用。"""
    client.post("/api/register", json={"username": "zhang", "password": "secret123"})
    client.post("/api/login", json={"username": "zhang", "password": "secret123"})
    assert client.get("/api/surveys").status_code == 200

    logout = client.post("/api/logout")

    assert logout.status_code == 200
    assert client.get("/api/surveys").status_code == 401
