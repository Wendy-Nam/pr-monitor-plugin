"""이메일 발송 채널 추상화 — provider 라우팅 테스트.

send-email.py 는 `email.provider`(microsoft_graph | smtp)에 따라 발송을 분기한다.
실제 발송(네트워크)은 테스트하지 않고, 디스패치·설정 파싱·하위호환·에러 경로만 본다.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location(
        "send_email_mod", ROOT / "scripts" / "send-email.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestEmailProvider:
    def test_default_provider_is_graph(self):
        """provider 미지정 → microsoft_graph (하위호환)."""
        m = _mod()
        assert m._provider({}) == "microsoft_graph"
        assert m._provider({"email": {}}) == "microsoft_graph"

    def test_smtp_provider_selected(self):
        m = _mod()
        assert m._provider({"email": {"provider": "smtp"}}) == "smtp"

    def test_unknown_provider_errors_clearly(self):
        m = _mod()
        r = m.send_mail({"email": {"provider": "carrier_pigeon"}},
                        "s", "<p>h</p>", ["a@b.com"])
        assert r["ok"] is False and r["code"] == "bad_provider"

    def test_smtp_missing_config_errors(self):
        m = _mod()
        r = m.send_mail({"email": {"provider": "smtp", "from": "x@y.com"}},
                        "s", "<p>h</p>", ["a@b.com"])
        assert r["ok"] is False and "SMTP 설정 없음" in r["msg"]

    def test_smtp_config_parse(self):
        m = _mod()
        s = m._smtp_cfg({"email": {"smtp": {
            "host": "smtp.gmail.com", "port": 465,
            "user": "u", "password": "p", "use_ssl": True}}})
        assert s["host"] == "smtp.gmail.com" and s["port"] == 465 and s["use_ssl"] is True
