#!/usr/bin/env python3
"""
PR Monitor — Microsoft Graph 이메일 발송.

config/delivery.yaml 에서 Azure 인증 + 수신자를 읽는다.

사용법:
  # 수신 그룹 지정 발송
  python3 scripts/send-email.py \
      --group newsletter_briefing \
      --subject "[일일 브리핑] 2026-06-05" \
      --html data/output/daily-2026-06-05.html

  # 테스트 발송 (특정 주소로)
  python3 scripts/send-email.py --test --to your@email.com

  # 키 에러 알림 (alerts 그룹으로)
  python3 scripts/send-email.py --notify-key-error "Azure client_secret 만료"

  # 수신자 목록 확인
  python3 scripts/send-email.py --list-recipients
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

try:
    import requests
except ImportError as e:
    print(f"ERROR: 패키지 필요 — pip install requests pyyaml ({e})", file=sys.stderr)
    sys.exit(2)

from lib.common import CONFIG_DIR, load_yaml

DELIVERY_YAML = CONFIG_DIR / "delivery.yaml"


def load_delivery_config() -> dict:
    """Recipients/pilot config from delivery.yaml. Secrets come from env now, so
    a missing file is tolerated (returns {}) — only recipient-dependent actions
    error later. Plugin model: secrets via userConfig→keychain, this file holds
    only non-secret recipient groups."""
    if not DELIVERY_YAML.exists():
        return {}
    return load_yaml(DELIVERY_YAML)


def _env(*names: str) -> str | None:
    """First non-empty value among the given env var names."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def _azure_creds(cfg: dict) -> tuple[str | None, str | None, str | None]:
    """(tenant_id, client_id, client_secret) — env (userConfig→keychain) first,
    then delivery.yaml's email.azure block (dev fallback)."""
    azure = (cfg.get("email") or {}).get("azure") or {}
    tenant = _env("CLAUDE_PLUGIN_OPTION_AZURE_TENANT_ID", "AZURE_TENANT_ID") or azure.get("tenant_id")
    client_id = _env("CLAUDE_PLUGIN_OPTION_AZURE_CLIENT_ID", "AZURE_CLIENT_ID") or azure.get("client_id")
    client_secret = _env("CLAUDE_PLUGIN_OPTION_AZURE_CLIENT_SECRET", "AZURE_CLIENT_SECRET") or azure.get("client_secret")
    return tenant, client_id, client_secret


def _sender(cfg: dict) -> str | None:
    return _env("CLAUDE_PLUGIN_OPTION_EMAIL_FROM", "EMAIL_FROM") or (cfg.get("email") or {}).get("from")


def _provider(cfg: dict) -> str:
    """발송 채널 — delivery.yaml email.provider (기본 microsoft_graph, 하위호환)."""
    return (_env("PRM_EMAIL_PROVIDER")
            or (cfg.get("email") or {}).get("provider")
            or "microsoft_graph").lower()


def _smtp_cfg(cfg: dict) -> dict:
    """SMTP 설정 — env(userConfig→키체인) 우선, delivery.yaml email.smtp 폴백.
    Gmail·O365·SES·사내 메일 등 표준 SMTP 서버를 stdlib 로 지원(새 의존성 없음)."""
    smtp = (cfg.get("email") or {}).get("smtp") or {}
    return {
        "host": _env("CLAUDE_PLUGIN_OPTION_SMTP_HOST", "SMTP_HOST") or smtp.get("host"),
        "port": int(_env("CLAUDE_PLUGIN_OPTION_SMTP_PORT", "SMTP_PORT") or smtp.get("port") or 587),
        "user": _env("CLAUDE_PLUGIN_OPTION_SMTP_USER", "SMTP_USER") or smtp.get("user"),
        "password": _env("CLAUDE_PLUGIN_OPTION_SMTP_PASSWORD", "SMTP_PASSWORD") or smtp.get("password"),
        # 기본 STARTTLS(587). 465 면 implicit SSL 로 자동 전환.
        "use_ssl": str(smtp.get("use_ssl", "")).lower() in ("1", "true", "yes"),
    }


def get_graph_token(cfg: dict) -> tuple[str | None, str | None]:
    """(access_token, error_msg) — 성공 시 (token, None), 실패 시 (None, msg)."""
    tenant, client_id, client_secret = _azure_creds(cfg)
    if not (tenant and client_id and client_secret):
        return None, ("Azure 인증값 없음 — 플러그인 설정(userConfig: azure_tenant_id/"
                      "azure_client_id/azure_client_secret) 또는 config/delivery.yaml 확인")

    r = requests.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=10,
    )
    if not r.ok:
        err = r.json().get("error_description", r.text[:300])
        return None, f"Graph 토큰 실패 — {err}"

    return r.json()["access_token"], None


def resolve_recipients(cfg: dict, group: str | None = None,
                       to_override: str | None = None) -> list[str]:
    if to_override:
        return [e.strip() for e in to_override.split(",") if e.strip()]

    if cfg.get("pilot_mode"):
        alerts = cfg["recipients"].get("alerts", {}).get("to", [])
        if alerts:
            return alerts

    if group and group in cfg["recipients"]:
        return cfg["recipients"][group].get("to", [])

    return []


def send_mail(cfg: dict, subject: str, html_body: str,
              to_emails: list[str],
              attachments: list[Path] | None = None) -> dict:
    """발송 디스패처 — provider 에 따라 Graph / SMTP 로 라우팅. 호출부는 동일 시그니처."""
    provider = _provider(cfg)
    if provider == "smtp":
        return send_mail_smtp(cfg, subject, html_body, to_emails, attachments)
    if provider in ("microsoft_graph", "graph"):
        return send_mail_graph(cfg, subject, html_body, to_emails, attachments)
    return {"ok": False, "status": 0, "code": "bad_provider",
            "msg": f"알 수 없는 email.provider '{provider}' (microsoft_graph | smtp)"}


def send_mail_smtp(cfg: dict, subject: str, html_body: str,
                   to_emails: list[str],
                   attachments: list[Path] | None = None) -> dict:
    """표준 SMTP 발송 (stdlib). Gmail/O365/SES/사내 메일 등 — Azure 비종속 경로."""
    s = _smtp_cfg(cfg)
    sender = _sender(cfg)
    if not (s["host"] and sender):
        return {"ok": False, "status": 0, "code": "auth_failed",
                "msg": "SMTP 설정 없음 — email.smtp.host + email.from(또는 env) 필요"}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to_emails)
    msg.set_content("HTML 메일입니다. HTML 지원 클라이언트로 확인하세요.")
    msg.add_alternative(html_body, subtype="html")
    for p in (attachments or []):
        if p.exists():
            msg.add_attachment(p.read_bytes(), maintype="application",
                               subtype="octet-stream", filename=p.name)

    try:
        if s["use_ssl"] or s["port"] == 465:
            with smtplib.SMTP_SSL(s["host"], s["port"], context=ssl.create_default_context(), timeout=20) as srv:
                if s["user"]:
                    srv.login(s["user"], s["password"] or "")
                srv.send_message(msg)
        else:
            with smtplib.SMTP(s["host"], s["port"], timeout=20) as srv:
                srv.starttls(context=ssl.create_default_context())
                if s["user"]:
                    srv.login(s["user"], s["password"] or "")
                srv.send_message(msg)
        return {"ok": True, "status": 202, "msg": "발송 완료 (SMTP)"}
    except (smtplib.SMTPException, OSError) as e:
        return {"ok": False, "status": 0, "code": "smtp_error", "msg": f"SMTP 발송 실패 — {e}"}


def send_mail_graph(cfg: dict, subject: str, html_body: str,
                    to_emails: list[str],
                    attachments: list[Path] | None = None) -> dict:
    token, err = get_graph_token(cfg)
    if err:
        return {"ok": False, "status": 0, "msg": err, "code": "auth_failed"}

    sender = _sender(cfg)

    # 첨부파일 빌드
    attach_list = []
    for p in (attachments or []):
        if p.exists():
            content_bytes = base64.b64encode(p.read_bytes()).decode("ascii")
            attach_list.append({
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": p.name,
                "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                               if p.suffix == ".xlsx" else "application/octet-stream",
                "contentBytes": content_bytes,
            })

    message = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [
                {"emailAddress": {"address": e}} for e in to_emails
            ],
        },
        "saveToSentItems": "true",
    }
    if attach_list:
        message["message"]["attachments"] = attach_list

    r = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=message,
        timeout=15,
    )

    if r.status_code == 202:
        return {"ok": True, "status": 202, "msg": "발송 완료"}
    err = r.json().get("error", {})
    return {
        "ok": False,
        "status": r.status_code,
        "msg": err.get("message", r.text[:300]),
        "code": err.get("code", ""),
    }


def send_key_error_notification(cfg: dict, error_msg: str):
    recipients = resolve_recipients(cfg, group="alerts")
    if not recipients:
        print("WARN: alerts 수신자 없음", file=sys.stderr)
        return

    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto;">
      <h2 style="color: #d32f2f;">⚠️ PR Monitor API 키 문제 감지</h2>
      <p>오늘 브리핑 실행 중 문제가 발생했습니다:</p>
      <div style="background: #fff3e0; border-left: 4px solid #ff9800; padding: 12px 16px; margin: 16px 0;">
        <strong>{error_msg}</strong>
      </div>
      <p>config/delivery.yaml 을 편집해 갱신할 수 있습니다:</p>
      <pre style="background: #f5f5f5; padding: 12px; border-radius: 4px;">"API 상태 보여줘"</pre>
      <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 24px 0;">
      <p style="color: #666; font-size: 13px;">
        나머지 소스는 정상 수집되었으며, 해당 소스를 제외한 브리핑이 발송됩니다.
      </p>
    </div>
    """
    result = send_mail(cfg, "[PR Monitor 알림] API 키 갱신 필요", html, recipients)
    if result["ok"]:
        print(f"키 에러 알림 발송 완료 → {', '.join(recipients)}", file=sys.stderr)
    else:
        print(f"키 에러 알림 발송 실패: {result['msg']}", file=sys.stderr)


def list_recipients(cfg: dict):
    pilot = cfg.get("pilot_mode", False)
    print(f"파일럿 모드: {'ON (alerts 그룹으로만 발송)' if pilot else 'OFF'}")
    if pilot:
        remaining = cfg.get("pilot_runs_remaining", "?")
        print(f"  잔여 파일럿 실행: {remaining}회\n")
    for key, group in cfg.get("recipients", {}).items():
        name = group.get("name", key)
        freq = group.get("frequency", "")
        to = group.get("to", [])
        print(f"[{key}] {name}")
        if freq:
            print(f"  빈도: {freq}")
        for addr in to:
            print(f"  → {addr}")
        print()


def validate_connection(cfg: dict) -> dict:
    """provider 에 맞는 연결 검증으로 분기."""
    provider = _provider(cfg)
    if provider == "smtp":
        return validate_smtp(cfg)
    if provider in ("microsoft_graph", "graph"):
        return validate_graph(cfg)
    return {"ok": False, "msg": f"알 수 없는 email.provider '{provider}'"}


def validate_smtp(cfg: dict) -> dict:
    s = _smtp_cfg(cfg)
    sender = _sender(cfg)
    if not (s["host"] and sender):
        return {"ok": False, "msg": "SMTP 설정 없음 — email.smtp.host + email.from 필요"}
    try:
        if s["use_ssl"] or s["port"] == 465:
            srv = smtplib.SMTP_SSL(s["host"], s["port"], context=ssl.create_default_context(), timeout=10)
        else:
            srv = smtplib.SMTP(s["host"], s["port"], timeout=10)
            srv.starttls(context=ssl.create_default_context())
        with srv:
            if s["user"]:
                srv.login(s["user"], s["password"] or "")
        return {"ok": True, "msg": f"SMTP 연결 정상 ({s['host']}:{s['port']}, 발송자: {sender})"}
    except (smtplib.SMTPException, OSError) as e:
        return {"ok": False, "msg": f"SMTP 연결 실패 — {e}"}


def validate_graph(cfg: dict) -> dict:
    token, err = get_graph_token(cfg)
    if err:
        return {"ok": False, "msg": err}
    sender = _sender(cfg)
    r = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"message": {"subject": ""}},
        timeout=10,
    )
    if r.status_code in (400, 202):
        return {"ok": True, "msg": f"Graph 연결 정상 (발송자: {sender})"}
    err_obj = r.json().get("error", {})
    code = err_obj.get("code", "")
    if "MailboxNotFound" in code or "ResourceNotFound" in code:
        return {"ok": False, "msg": f"{sender} 사서함을 찾을 수 없음"}
    if "Authorization" in err_obj.get("message", ""):
        return {"ok": False, "msg": "Mail.Send 권한 없음 — 관리자에게 권한 부여 요청"}
    return {"ok": False, "msg": f"[{r.status_code}] {err_obj.get('message', '')[:200]}"}


def main():
    parser = argparse.ArgumentParser(description="PR Monitor 이메일 발송")
    parser.add_argument("--group", type=str,
                        help="수신 그룹 (newsletter_briefing / marketing_pr_list / weekly_newsletter)")
    parser.add_argument("--subject", type=str, default="[PR Monitor] 테스트")
    parser.add_argument("--html", type=str, help="HTML 파일 경로")
    parser.add_argument("--body", type=str, help="HTML 문자열 직접 전달")
    parser.add_argument("--to", type=str, help="수신자 직접 지정 (쉼표 구분, group 무시)")
    parser.add_argument("--test", action="store_true", help="테스트 메일 발송")
    parser.add_argument("--notify-key-error", type=str, help="키 에러 알림 발송")
    parser.add_argument("--list-recipients", action="store_true", help="수신자 목록 출력")
    parser.add_argument("--validate", action="store_true", help="발송 채널(Graph/SMTP) 연결 검증만")
    parser.add_argument("--attachment", type=str, action="append",
                        help="첨부파일 경로 (반복 사용 가능)", dest="attachments")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 출력")
    args = parser.parse_args()

    cfg = load_delivery_config()

    if args.list_recipients:
        list_recipients(cfg)
        return

    if args.validate:
        result = validate_connection(cfg)
        status = "✅" if result["ok"] else "❌"
        print(f"{status} {result['msg']}")
        sys.exit(0 if result["ok"] else 1)

    if args.notify_key_error:
        send_key_error_notification(cfg, args.notify_key_error)
        return

    recipients = resolve_recipients(cfg, group=args.group, to_override=args.to)
    if not recipients:
        print("ERROR: 수신자 없음. --to 또는 --group 지정 필요", file=sys.stderr)
        sys.exit(1)

    if args.test:
        html = f"""
        <div style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto;">
          <h2>✅ PR Monitor 이메일 테스트</h2>
          <p>이 메일이 도착했다면 Microsoft Graph 연동이 정상입니다.</p>
          <p style="color: #888; font-size: 13px;">
            발송: {_sender(cfg)}<br>
            수신: {', '.join(recipients)}<br>
            파일럿 모드: {'ON' if cfg.get('pilot_mode') else 'OFF'}
          </p>
        </div>
        """
    elif args.html:
        html_path = Path(args.html)
        if not html_path.exists():
            print(f"ERROR: 파일 없음 — {args.html}", file=sys.stderr)
            sys.exit(1)
        html = html_path.read_text(encoding="utf-8")
    elif args.body:
        html = args.body
    else:
        print("ERROR: --html, --body, 또는 --test 중 하나 필요", file=sys.stderr)
        sys.exit(1)

    if cfg.get("pilot_mode"):
        print(f"📋 파일럿 모드 — alerts 그룹으로 발송: {recipients}", file=sys.stderr)

    attach_paths = [Path(a) for a in (args.attachments or [])]
    result = send_mail(cfg, args.subject, html, recipients, attachments=attach_paths)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        if result["ok"]:
            print(f"✅ 발송 완료 → {', '.join(recipients)}")
        else:
            print(f"❌ 발송 실패: [{result['status']}] {result['msg']}")
            sys.exit(1)


if __name__ == "__main__":
    main()
