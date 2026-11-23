import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("checking_notice_dispatch", Path(__file__).with_name("publish_scheduled_notices.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def container():
    return [{"Name": "/me-nu-checking-api-1", "State": {"Running": True}, "Config": {
        "Labels": {"com.docker.compose.project": "me-nu-checking", "com.docker.compose.service": "api"},
        "Env": ["MENU_DEPLOYMENT_ENV=checking"], "Image": "me-nu-backend-checking:" + "a" * 40}}]


def test_only_exact_running_checking_api_is_accepted():
    module.validate_container(container())
    for key, value in (("Name", "/me-nu-main-api-1"), ("State", {"Running": False})):
        item = container(); item[0][key] = value
        with pytest.raises(module.DispatchError): module.validate_container(item)
    item = container(); item[0]["Config"]["Env"] = ["MENU_DEPLOYMENT_ENV=main"]
    with pytest.raises(module.DispatchError): module.validate_container(item)
    item = container(); item[0]["Config"]["Image"] = "me-nu-backend-checking:latest"
    with pytest.raises(module.DispatchError): module.validate_container(item)


def test_receipt_emits_counts_only_and_rejects_bad_counts():
    value = {"mode": "apply", "due": 1, "published": 1, "deliveries": 5, "privateBody": "must be removed"}
    assert module.safe_receipt(value, apply=True) == {k: value[k] for k in ("mode", "due", "published", "deliveries")}
    for change in ({"deliveries": True}, {"published": -1}, {"mode": "preview"}):
        with pytest.raises(module.DispatchError): module.safe_receipt(value | change, apply=True)
