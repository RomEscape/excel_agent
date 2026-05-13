"""Gmail OAuth and email endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from office_claw_sidecar.services.gmail_service import GmailService
from office_claw_sidecar.services.filter_service import FilterService
from office_claw_sidecar.services.llm_service import get_llm_service

router = APIRouter()
gmail_svc = GmailService()
filter_svc = FilterService()


@router.get("/status")
async def gmail_status():
    """Check Gmail connection status."""
    connected = gmail_svc.is_connected()
    return {"connected": connected}


@router.post("/connect")
async def gmail_connect():
    """Start OAuth 2.0 flow to connect Gmail."""
    try:
        result = gmail_svc.start_oauth_flow()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disconnect")
async def gmail_disconnect():
    """Disconnect Gmail account."""
    gmail_svc.disconnect()
    return {"status": "disconnected"}


@router.get("/emails")
async def get_emails(max_results: int = 20):
    """Fetch recent emails from inbox with importance classification."""
    try:
        emails = gmail_svc.fetch_recent_emails(max_results)
        classified = filter_svc.classify_emails(emails)
        return {"emails": classified, "count": len(classified)}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/emails/{message_id}/body")
async def get_email_body(message_id: str):
    """Get the full body of a specific email."""
    try:
        body = gmail_svc.get_email_body(message_id)
        return {"id": message_id, "body": body}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/emails/{message_id}/summarize")
async def summarize_email(message_id: str):
    """Summarize a specific email using the configured LLM provider."""
    try:
        body = gmail_svc.get_email_body(message_id)
        if not body.strip():
            return {"id": message_id, "summary": "(본문 없음)"}

        # Truncate long emails to fit context window
        truncated = body[:4000] if len(body) > 4000 else body

        prompt = (
            "다음 이메일을 한국어로 3줄 이내로 요약해줘. "
            "핵심 내용, 발신자의 의도, 필요한 액션이 있으면 포함해.\n\n"
            f"---\n{truncated}\n---"
        )

        llm = get_llm_service()
        summary = await llm.chat([{"role": "user", "content": prompt}])
        return {"id": message_id, "summary": summary}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/emails/summarize-batch")
async def summarize_batch(max_results: int = 5):
    """Fetch recent emails and summarize each one using the configured LLM provider."""
    try:
        emails = gmail_svc.fetch_recent_emails(max_results)
        summaries = []
        llm = get_llm_service()

        for email in emails:
            try:
                body = gmail_svc.get_email_body(email["id"])
                if not body.strip():
                    summaries.append({**email, "summary": "(본문 없음)"})
                    continue

                truncated = body[:4000] if len(body) > 4000 else body
                prompt = (
                    "다음 이메일을 한국어로 2줄 이내로 요약해줘.\n\n"
                    f"---\n{truncated}\n---"
                )
                summary = await llm.chat([{"role": "user", "content": prompt}])
                summaries.append({**email, "summary": summary})
            except Exception as e:
                summaries.append({**email, "summary": f"(요약 실패: {e})"})

        return {"emails": summaries, "count": len(summaries)}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PrioritizeRequest(BaseModel):
    email_ids: list[str]


@router.post("/emails/{message_id}/draft-reply")
async def draft_reply(message_id: str):
    """Generate a professional Korean reply draft for a given email."""
    try:
        body = gmail_svc.get_email_body(message_id)
        truncated = body[:3000] if len(body) > 3000 else body

        prompt = (
            "아래 이메일에 대한 전문적이고 공손한 한국어 답장 초안을 작성해줘.\n"
            "답장은 인사말로 시작하고, 본문 내용에 적절히 응답하며, 마무리 인사로 끝내줘.\n"
            "마크다운 없이 일반 텍스트로 작성해줘.\n\n"
            f"--- 원본 이메일 ---\n{truncated}\n---"
        )

        llm = get_llm_service()
        draft = await llm.chat([{"role": "user", "content": prompt}])
        return {"draft": draft}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/emails/prioritize")
async def prioritize_emails(req: PrioritizeRequest):
    """Classify a list of emails as 긴급 / 일반 / FYI with a one-line reason."""
    try:
        # Fetch once and build a lookup dict — avoids N+1 Gmail API calls.
        # Cap at 10 emails to keep the LLM prompt within context limits.
        target_ids = req.email_ids[:10]
        try:
            all_emails = gmail_svc.fetch_recent_emails(50)
            email_map = {e.get("id"): e for e in all_emails}
        except Exception:
            email_map = {}

        summaries = []
        for email_id in target_ids:
            meta = email_map.get(email_id)
            if meta:
                subject = meta.get("subject", "(제목 없음)")
                sender = meta.get("from", meta.get("sender", "알 수 없음"))
                summaries.append(f"- ID: {email_id} | 제목: {subject} | 발신자: {sender}")
            else:
                summaries.append(f"- ID: {email_id} | 제목: (불러오기 실패)")

        email_list = "\n".join(summaries)
        prompt = (
            "아래 이메일 목록을 읽고 각 이메일을 '긴급', '일반', 'FYI' 중 하나로 분류하고,\n"
            "한 줄로 분류 이유를 설명해줘.\n"
            "반드시 아래 JSON 형식으로만 응답해줘 (다른 텍스트 없이):\n"
            '[{"id":"...", "tag":"긴급|일반|FYI", "reason":"..."}]\n\n'
            f"이메일 목록:\n{email_list}"
        )

        llm = get_llm_service()
        raw = await llm.chat([{"role": "user", "content": prompt}])

        # Safely extract JSON array from response
        import json
        import re
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            priorities = json.loads(match.group())
        else:
            # Fallback: mark all as 일반 if LLM did not produce JSON
            priorities = [
                {"id": eid, "tag": "일반", "reason": "자동 분류 불가"}
                for eid in req.email_ids
            ]

        return {"priorities": priorities}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/filter-rules")
async def get_filter_rules():
    """Get current keyword filter rules."""
    return filter_svc.get_rules()


@router.put("/filter-rules")
async def update_filter_rules(rules: dict):
    """Update keyword filter rules."""
    try:
        updated = filter_svc.update_rules(rules)
        return updated
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
