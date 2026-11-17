from backend.common.utils.enums import LlmMode, LlmTask
from backend.common.utils.exceptions import LlmDisabledError
from backend.services.commitments.core.rules import extract_rules
from backend.services.llm.core.provider_gateway import ProviderGateway


async def extract_commitments(
    text: str,
    *,
    gateway: ProviderGateway | None,
) -> list[dict]:
    drafts = [d.model_dump() for d in extract_rules(text)]
    if gateway is None or gateway.mode == LlmMode.DEGRADED:
        return drafts
    try:
        result = await gateway.complete(
            f"List commitments as one line each:\n\n{text[:4000]}",
            task=LlmTask.EXTRACT,
        )
        if result.text.strip():
            drafts.append(
                {
                    "summary": result.text.strip()[:500],
                    "actor": "me",
                    "confidence": 0.75,
                    "extraction_mode": "live",
                }
            )
    except (LlmDisabledError, NotImplementedError, ValueError):
        return drafts
    return drafts
