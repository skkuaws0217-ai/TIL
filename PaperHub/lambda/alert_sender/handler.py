"""
PaperHub — 알림 메일 발송 Lambda (SES)
Step Functions 워크플로우의 마지막 단계로 호출됩니다.

기능:
1. 요약 결과를 HTML 이메일 템플릿에 렌더링
2. 원본 PDF + 요약 PDF를 S3 Presigned URL로 첨부
3. SES로 알림 메일 발송
"""

import os
import json
import boto3
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# ─── 환경 변수 ───
ALERTS_TABLE = os.environ.get("ALERTS_TABLE", "paperhub-alerts")
BUCKET_NAME = os.environ.get("PDF_BUCKET", "paperhub-pdfs")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "alert@paperhub.io")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://paperhub.io")

# ─── AWS 클라이언트 ───
ses = boto3.client("ses")
s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
alerts_table = dynamodb.Table(ALERTS_TABLE)


def get_presigned_url(s3_key: str, expiration: int = 86400) -> str:
    """S3 Presigned URL 생성 (24시간 유효)"""
    if not s3_key:
        return ""
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": s3_key},
            ExpiresIn=expiration,
        )
    except Exception:
        return ""


def get_subscribers(keyword: str) -> list[dict]:
    """특정 키워드의 활성 구독자 조회"""
    response = alerts_table.scan(
        FilterExpression="keyword = :kw AND active = :val",
        ExpressionAttributeValues={":kw": keyword, ":val": True},
    )
    return response.get("Items", [])


def build_email_html(paper: dict) -> str:
    """알림 메일 HTML 템플릿 생성"""

    title = paper.get("title", "")
    authors = ", ".join(paper.get("authors", [])[:3])
    if len(paper.get("authors", [])) > 3:
        authors += " et al."
    journal = paper.get("journal", "")
    year = paper.get("year", "")
    doi = paper.get("doi", "")
    keyword = paper.get("alert_keyword", "")
    oneline = paper.get("summary_oneline", "")
    full_summary = paper.get("summary_full", "")
    pdf_url = paper.get("pdf_presigned_url", "")
    summary_pdf_url = paper.get("summary_pdf_presigned_url", "")

    today = datetime.now().strftime("%Y년 %m월 %d일")

    # 전문 요약 HTML 포맷팅
    full_summary_html = full_summary.replace("\n", "<br>") if full_summary else ""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ margin:0; padding:0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0b0f1a; color: #e2e8f0; }}
  .container {{ max-width: 600px; margin: 0 auto; padding: 24px; }}
  .header {{ background: linear-gradient(135deg, #059669, #6ee7b7); padding: 24px; border-radius: 14px 14px 0 0; text-align: center; }}
  .header h1 {{ color: #0f172a; margin: 0; font-size: 22px; font-weight: 700; }}
  .header p {{ color: #064e3b; margin: 6px 0 0; font-size: 13px; }}
  .body {{ background: #111827; border: 1px solid #1e293b; border-top: none; border-radius: 0 0 14px 14px; padding: 28px; }}
  .badge {{ display: inline-block; background: #6ee7b718; color: #6ee7b7; font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 20px; border: 1px solid #6ee7b740; }}
  .meta {{ color: #94a3b8; font-size: 12px; margin: 12px 0 16px; }}
  .meta .journal {{ color: #60a5fa; font-weight: 500; }}
  .paper-card {{ background: #0b0f1a; border: 1px solid #1e293b; border-radius: 10px; padding: 20px; margin: 16px 0; }}
  .paper-title {{ font-size: 16px; font-weight: 600; line-height: 1.5; margin-bottom: 8px; }}
  .summary-box {{ padding: 14px; border-radius: 8px; margin: 12px 0; }}
  .summary-oneline {{ background: #111827; border-left: 3px solid #6ee7b7; }}
  .summary-full {{ background: #111827; border-left: 3px solid #a78bfa; }}
  .summary-label {{ font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }}
  .summary-text {{ font-size: 13px; line-height: 1.7; }}
  .attachment {{ display: flex; align-items: center; gap: 12px; padding: 12px 16px; background: #0b0f1a; border: 1px solid #1e293b; border-radius: 10px; margin: 8px 0; text-decoration: none; color: #e2e8f0; }}
  .attachment:hover {{ border-color: #6ee7b7; }}
  .att-icon {{ width: 36px; height: 36px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 16px; }}
  .att-name {{ font-weight: 500; font-size: 13px; }}
  .att-size {{ font-size: 11px; color: #94a3b8; }}
  .btn {{ display: inline-block; padding: 10px 24px; background: #6ee7b7; color: #0f172a; font-weight: 600; font-size: 14px; border-radius: 8px; text-decoration: none; margin: 8px 4px 8px 0; }}
  .btn:hover {{ background: #34d399; }}
  .btn-secondary {{ background: transparent; color: #e2e8f0; border: 1px solid #1e293b; }}
  .footer {{ text-align: center; padding: 20px; font-size: 11px; color: #64748b; }}
  .footer a {{ color: #6ee7b7; text-decoration: none; }}
  .pipeline {{ display: flex; align-items: center; justify-content: center; gap: 6px; flex-wrap: wrap; padding: 12px; background: #1a2235; border-radius: 8px; margin: 16px 0; font-size: 10px; }}
  .pipe-node {{ padding: 4px 10px; border-radius: 6px; font-weight: 600; white-space: nowrap; }}
  .pipe-arrow {{ color: #64748b; }}
</style>
</head>
<body>
<div class="container">
  <!-- 헤더 -->
  <div class="header">
    <h1>📄 PaperHub 새 논문 알림</h1>
    <p>{today} · 키워드: "{keyword}"</p>
  </div>

  <div class="body">
    <div style="text-align: right; margin-bottom: 12px;">
      <span class="badge">AUTO ALERT</span>
    </div>

    <!-- 논문 카드 -->
    <div class="paper-card">
      <div class="paper-title">{title}</div>
      <div class="meta">
        <span class="journal">{journal}</span> · {year} · {authors}
        <br>DOI: {doi}
      </div>

      <!-- 한줄 요약 -->
      <div class="summary-box summary-oneline">
        <div class="summary-label" style="color: #6ee7b7;">📝 AI 한줄 요약</div>
        <div class="summary-text">{oneline}</div>
      </div>

      <!-- 전문 요약 -->
      <div class="summary-box summary-full">
        <div class="summary-label" style="color: #a78bfa;">📄 AI 전문 요약 (1페이지)</div>
        <div class="summary-text">{full_summary_html}</div>
      </div>
    </div>

    <!-- 다운로드 버튼 -->
    <div style="margin: 20px 0;">
      {"<a href='" + pdf_url + "' class='btn'>📥 원본 PDF 다운로드</a>" if pdf_url else ""}
      {"<a href='" + summary_pdf_url + "' class='btn btn-secondary'>📋 요약본 PDF</a>" if summary_pdf_url else ""}
    </div>

    <!-- 첨부파일 목록 -->
    <div style="font-size: 11px; font-weight: 600; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 8px;">
      첨부파일
    </div>

    {"<a href='" + pdf_url + "' class='attachment'><div class='att-icon' style='background: #f8717120; color: #f87171;'>📄</div><div><div class='att-name'>원본 논문 (Full PDF)</div><div class='att-size'>링크 클릭 시 다운로드 (24시간 유효)</div></div></a>" if pdf_url else ""}

    {"<a href='" + summary_pdf_url + "' class='attachment'><div class='att-icon' style='background: #6ee7b720; color: #6ee7b7;'>✨</div><div><div class='att-name'>AI 요약본 (한줄 + 전문)</div><div class='att-size'>링크 클릭 시 다운로드 (24시간 유효)</div></div></a>" if summary_pdf_url else ""}

    <!-- 처리 파이프라인 -->
    <div class="pipeline">
      <span class="pipe-node" style="background: #fbbf2420; color: #fbbf24;">⏰ EventBridge</span>
      <span class="pipe-arrow">→</span>
      <span class="pipe-node" style="background: #60a5fa20; color: #60a5fa;">⚡ Lambda</span>
      <span class="pipe-arrow">→</span>
      <span class="pipe-node" style="background: #a78bfa20; color: #a78bfa;">🔬 PubMed</span>
      <span class="pipe-arrow">→</span>
      <span class="pipe-node" style="background: #f472b620; color: #f472b6;">🤖 Bedrock</span>
      <span class="pipe-arrow">→</span>
      <span class="pipe-node" style="background: #6ee7b720; color: #6ee7b7;">📧 SES</span>
    </div>
  </div>

  <div class="footer">
    이 알림은 PaperHub 키워드 알림 서비스에 의해 자동 발송되었습니다.<br>
    알림 설정 변경: <a href="{FRONTEND_URL}/alerts">PaperHub 대시보드</a><br><br>
    © 2025 PaperHub — Academic Paper Access Service
  </div>
</div>
</body>
</html>"""


def send_email(recipient: str, paper: dict):
    """SES로 알림 이메일 발송"""

    keyword = paper.get("alert_keyword", "")
    title = paper.get("title", "")

    html_body = build_email_html(paper)

    # 텍스트 폴백
    text_body = f"""PaperHub 새 논문 알림

키워드: {keyword}
제목: {title}
DOI: {paper.get('doi', '')}

한줄 요약:
{paper.get('summary_oneline', '')}

전문 요약:
{paper.get('summary_full', '')}

---
PaperHub — Academic Paper Access Service
"""

    try:
        ses.send_email(
            Source=SENDER_EMAIL,
            Destination={"ToAddresses": [recipient]},
            Message={
                "Subject": {
                    "Data": f"🔔 PaperHub Alert — [{keyword}] {title[:60]}",
                    "Charset": "UTF-8",
                },
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )
        print(f"[EMAIL] 발송 완료: {recipient}")
        return True

    except Exception as e:
        print(f"[ERROR] 메일 발송 실패 ({recipient}): {e}")
        return False


def handler(event, context):
    """
    Step Functions에서 호출되는 메일 발송 Lambda

    Input (from summarizer):
    {
        "pmid": "...",
        "title": "...",
        "summary_oneline": "...",
        "summary_full": "...",
        "pdf_s3_key": "...",
        "summary_pdf_s3_key": "...",
        "alert_keyword": "CRISPR"
    }
    """

    print(f"[START] 메일 발송: {event.get('pmid')}")

    keyword = event.get("alert_keyword", "")
    if not keyword:
        print("[SKIP] alert_keyword 없음, 메일 발송 건너뜀")
        return {"statusCode": 200, "sent": 0}

    # Presigned URL 생성
    event["pdf_presigned_url"] = get_presigned_url(event.get("pdf_s3_key", ""))
    event["summary_pdf_presigned_url"] = get_presigned_url(event.get("summary_pdf_s3_key", ""))

    # 구독자 조회
    subscribers = get_subscribers(keyword)
    print(f"[SUBSCRIBERS] '{keyword}' 구독자: {len(subscribers)}명")

    sent_count = 0
    for sub in subscribers:
        email = sub.get("email", "")
        if email and send_email(email, event):
            sent_count += 1

    print(f"[DONE] 발송 완료: {sent_count}/{len(subscribers)}건")

    return {
        "statusCode": 200,
        "sent": sent_count,
        "total_subscribers": len(subscribers),
        "keyword": keyword,
        "pmid": event.get("pmid"),
    }
