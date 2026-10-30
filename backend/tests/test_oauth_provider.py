import logging
import traceback
from dataclasses import FrozenInstanceError
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from app.core.exceptions import ApiError
from app.services import oauth_provider
from app.services.oauth_provider import OAuthIdentity, OAuthProviderClient


@pytest.fixture(autouse=True)
def provider_settings(monkeypatch):
    monkeypatch.setattr(oauth_provider.settings, "oauth_public_api_origin", "https://api.test.me-nu.kr")
    for provider in ("kakao", "naver"):
        monkeypatch.setattr(oauth_provider.settings, f"{provider}_client_id", f"{provider}-client")
        monkeypatch.setattr(oauth_provider.settings, f"{provider}_client_secret", SecretStr("private-secret"))


def profile_for(provider):
    if provider == "kakao":
        return {
            "id": 123456789,
            "kakao_account": {
                "email": "Member@Example.com",
                "is_email_valid": True,
                "is_email_verified": True,
                "profile": {"nickname": " 회원 "},
            },
        }
    return {
        "resultcode": "00",
        "response": {"id": "unique-naver-member", "email": "Member@Example.com", "nickname": " 회원 "},
    }


async def exchange_with_profile(provider, profile):
    def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "private-token"})
        return httpx.Response(200, json=profile)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await OAuthProviderClient(client).exchange_identity(provider, "private-code", "opaque-state")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,host,path", [
    ("kakao", "kauth.kakao.com", "/oauth/authorize"),
    ("naver", "nid.naver.com", "/oauth2.0/authorize"),
])
async def test_authorization_url_uses_fixed_endpoint_encoded_callback_and_state(provider, host, path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected HTTP"))) as client:
        url = OAuthProviderClient(client).build_authorization_url(provider, "random+state&next=https://untrusted.test")
    parsed = urlsplit(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", host, path)
    assert parse_qs(parsed.query) == {
        "response_type": ["code"],
        "client_id": [f"{provider}-client"],
        "redirect_uri": [f"https://api.test.me-nu.kr/api/v1/auth/login/{provider}/callback"],
        "state": ["random+state&next=https://untrusted.test"],
    }
    assert "private-secret" not in url


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,token_url,profile_url,identifier", [
    ("kakao", "https://kauth.kakao.com/oauth/token", "https://kapi.kakao.com/v2/user/me", "123456789"),
    ("naver", "https://nid.naver.com/oauth2.0/token", "https://openapi.naver.com/v1/nid/me", "unique-naver-member"),
])
async def test_exchange_sends_secret_in_form_and_bearer_only_to_profile(
    provider, token_url, profile_url, identifier, caplog,
):
    caplog.set_level(logging.DEBUG)
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            assert request.method == "POST"
            assert str(request.url) == token_url
            assert request.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
            assert "Authorization" not in request.headers
            expected = {
                "grant_type": ["authorization_code"],
                "client_id": [f"{provider}-client"],
                "client_secret": ["private-secret"],
                "code": ["private-code+&="],
                "redirect_uri": [f"https://api.test.me-nu.kr/api/v1/auth/login/{provider}/callback"],
            }
            if provider == "naver":
                expected["state"] = ["random+state&="]
            assert parse_qs(request.content.decode()) == expected
            return httpx.Response(200, json={"access_token": "private-token"})
        assert request.method == "GET"
        assert str(request.url) == profile_url
        assert request.headers["Authorization"] == "Bearer private-token"
        assert request.content == b""
        return httpx.Response(200, json=profile_for(provider))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        identity = await OAuthProviderClient(client).exchange_identity(provider, "private-code+&=", "random+state&=")
    assert identity == OAuthIdentity(provider, identifier, "member@example.com", "회원")
    assert len(requests) == 2
    for private_value in ("private-secret", "private-token", "private-code"):
        assert private_value not in caplog.text
        assert all(private_value not in str(request.url) for request in requests)
    with pytest.raises(FrozenInstanceError):
        identity.provider_user_id = "different"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,profile", [
    ("kakao", {"id": 123}),
    ("naver", {"resultcode": "00", "response": {"id": "naver-id"}}),
])
async def test_identity_does_not_require_email_or_nickname(provider, profile):
    identity = await exchange_with_profile(provider, profile)
    assert identity.email is None
    assert identity.nickname is None


@pytest.mark.asyncio
@pytest.mark.parametrize("valid,verified", [(False, True), (True, False), (None, True), (True, None), (1, True), (True, "true")])
async def test_kakao_ignores_email_unless_both_verification_flags_are_true(valid, verified):
    profile = profile_for("kakao")
    profile["kakao_account"].update({"is_email_valid": valid, "is_email_verified": verified})
    identity = await exchange_with_profile("kakao", profile)
    assert identity.email is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["kakao", "naver"])
async def test_empty_nickname_is_optional(provider):
    profile = profile_for(provider)
    nickname_holder = profile["kakao_account"]["profile"] if provider == "kakao" else profile["response"]
    nickname_holder["nickname"] = " \t "
    assert (await exchange_with_profile(provider, profile)).nickname is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["kakao", "naver"])
@pytest.mark.parametrize("email", ["not-an-email", "Display Name <valid@example.com>", "", 123, ["valid@example.com"]])
async def test_invalid_supplied_email_is_rejected(provider, email):
    profile = profile_for(provider)
    email_holder = profile["kakao_account"] if provider == "kakao" else profile["response"]
    email_holder["email"] = email
    with pytest.raises(ApiError) as error:
        await exchange_with_profile(provider, profile)
    assert (error.value.status_code, error.value.code) == (401, "AUTH_FAILED")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,profile", [
    ("kakao", {"id": True}),
    ("kakao", {"id": "123"}),
    ("kakao", {"id": 1.5}),
    ("kakao", {"id": 0}),
    ("kakao", {"id": -1}),
    ("kakao", {"id": 123, "kakao_account": None}),
    ("kakao", {"id": 123, "kakao_account": {"profile": []}}),
    ("kakao", {"code": -401, "msg": "private-response", "id": 123}),
    ("naver", {"resultcode": "01", "response": {"id": "naver-id"}}),
    ("naver", {"response": {"id": "naver-id"}}),
    ("naver", {"resultcode": "00", "response": None}),
    ("naver", {"resultcode": "00", "response": {"id": 123}}),
    ("naver", {"resultcode": "00", "response": {"id": ""}}),
    ("naver", {"resultcode": "00", "response": {"id": " "}}),
    ("naver", {"resultcode": "00", "response": {"id": "id\n"}}),
])
async def test_malformed_provider_identity_is_rejected(provider, profile):
    with pytest.raises(ApiError) as error:
        await exchange_with_profile(provider, profile)
    assert (error.value.status_code, error.value.code) == (401, "AUTH_FAILED")
    assert "private-response" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {}, {"access_token": ""}, {"access_token": "  "}, {"access_token": None},
    {"access_token": 123}, {"access_token": "bad\r\ntoken"}, {"access_token": "한글"},
    {"access_token": "private-token", "error": "invalid_grant", "error_description": "private-response"},
    [], "private-response",
])
async def test_invalid_token_response_never_requests_profile(payload):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ApiError) as error:
            await OAuthProviderClient(client).exchange_identity("kakao", "private-code", "state")
    assert len(calls) == 1
    assert (error.value.status_code, error.value.code) == (401, "AUTH_FAILED")
    assert "private-response" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["token", "profile"])
@pytest.mark.parametrize("status,expected_status,expected_code", [
    (400, 401, "AUTH_FAILED"), (401, 401, "AUTH_FAILED"),
    (403, 401, "AUTH_FAILED"), (429, 503, "AUTH_UNAVAILABLE"),
    (500, 503, "AUTH_UNAVAILABLE"), (503, 503, "AUTH_UNAVAILABLE"),
])
async def test_http_errors_are_mapped_without_provider_response_data(stage, status, expected_status, expected_code, caplog):
    def handler(request):
        if stage == "profile" and request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "private-token"})
        return httpx.Response(status, json={"error_description": "private-secret private-token private-code"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ApiError) as error:
            await OAuthProviderClient(client).exchange_identity("naver", "private-code", "state")
    assert (error.value.status_code, error.value.code) == (expected_status, expected_code)
    for private_value in ("private-secret", "private-token", "private-code"):
        assert private_value not in str(error.value)
        assert private_value not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["token", "profile"])
@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
async def test_transport_failures_are_unavailable_without_exception_details(stage, failure, caplog):
    def handler(request):
        if stage == "profile" and request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "private-token"})
        raise failure("private-secret private-token private-code", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ApiError) as error:
            await OAuthProviderClient(client).exchange_identity("kakao", "private-code", "state")
    assert (error.value.status_code, error.value.code) == (503, "AUTH_UNAVAILABLE")
    rendered_error = "".join(traceback.format_exception(error.value))
    for private_value in ("private-secret", "private-token"):
        assert private_value not in rendered_error
        assert private_value not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["token", "profile"])
async def test_malformed_json_is_failed(stage):
    def handler(request):
        if stage == "profile" and request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "private-token"})
        return httpx.Response(200, content=b"not-json private-response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ApiError) as error:
            await OAuthProviderClient(client).exchange_identity("naver", "code", "state")
    assert (error.value.status_code, error.value.code) == (401, "AUTH_FAILED")
    assert "private-response" not in "".join(traceback.format_exception(error.value))


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["token", "profile"])
async def test_provider_redirect_is_not_followed_even_when_client_would_follow(stage):
    calls = []

    def handler(request):
        calls.append(request)
        if stage == "profile" and request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "private-token"})
        return httpx.Response(307, headers={"Location": "https://untrusted.test/steal"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(ApiError) as error:
            await OAuthProviderClient(client).exchange_identity("naver", "code", "state")
    assert (error.value.status_code, error.value.code) == (401, "AUTH_FAILED")
    assert len(calls) == (2 if stage == "profile" else 1)
    assert all(request.url.host != "untrusted.test" for request in calls)


@pytest.mark.asyncio
async def test_unknown_provider_is_rejected_before_network_or_credentials():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected HTTP"))) as client:
        adapter = OAuthProviderClient(client)
        with pytest.raises(ApiError) as error:
            adapter.build_authorization_url("../untrusted", "state")
        assert (error.value.status_code, error.value.code) == (401, "AUTH_FAILED")
        with pytest.raises(ApiError) as error:
            await adapter.exchange_identity("https://untrusted.test", "code", "state")
        assert (error.value.status_code, error.value.code) == (401, "AUTH_FAILED")


@pytest.mark.asyncio
@pytest.mark.parametrize("code,state", [("", "state"), (" \t", "state"), ("code", ""), ("code", "  ")])
async def test_blank_authorization_inputs_are_rejected_without_http(code, state):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected HTTP"))) as client:
        with pytest.raises(ApiError) as error:
            await OAuthProviderClient(client).exchange_identity("naver", code, state)
    assert (error.value.status_code, error.value.code) == (401, "AUTH_FAILED")
