"""CSRF 防护：服务端签发令牌，表单隐藏字段回传，写操作校验。

采用"双提交 Cookie"：同一随机令牌既写入 Cookie，又渲染进表单隐藏字段，
提交时服务端比较两者是否一致。攻击者站点受同源策略限制读不到受害者的
Cookie，因此无法伪造出字段与 Cookie 一致的请求。
"""

import hmac
import secrets

CSRF_COOKIE = "csrf_token"
CSRF_FIELD = "csrf_token"
_TOKEN_BYTES = 32


def issue_token() -> str:
    """签发一个新的不可预测令牌。"""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def tokens_match(cookie_value: str, form_value: str) -> bool:
    """Cookie 中的令牌与表单回传的令牌是否一致且非空。"""
    if not cookie_value or not form_value:
        return False
    return hmac.compare_digest(cookie_value, form_value)
