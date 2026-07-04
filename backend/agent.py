"""
agent.py — VOLTRIX Grid Intelligence Agent (Google ADK + google-genai)
- ADK agent with tools for stress analysis (needs function calling)
- google-genai direct streaming for chat (no tools needed, streaming SSE)

Exported functions (called by main.py):
  run_stress_analysis(stress_event, household_summaries) -> dict
  answer_question(question, context) -> str
  answer_question_stream(question, context) -> AsyncGenerator[str]
"""

import os
import json
import logging
import asyncio
from collections.abc import AsyncGenerator
from google import genai as genai_sdk
from google.genai import types
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from google.adk import Runner
from google.adk.sessions import InMemorySessionService

logger = logging.getLogger("voltrix.agent")

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
# ADK 2.x uses google.genai internally which reads GOOGLE_API_KEY
os.environ["GOOGLE_API_KEY"] = GEMINI_API_KEY


# ─── Tool functions (the agent calls these autonomously) ─────────────────────


def get_zone_details(
    zone_id: str, predicted_peak_kw: float, capacity_kw: float
) -> dict:
    overage = predicted_peak_kw - capacity_kw
    overage_pct = round((overage / capacity_kw) * 100, 1)
    return {
        "zone_id": zone_id,
        "predicted_peak_kw": predicted_peak_kw,
        "capacity_kw": capacity_kw,
        "overage_kw": round(overage, 2),
        "overage_pct": overage_pct,
        "risk_level": "critical" if predicted_peak_kw > capacity_kw else "moderate",
        "summary": (
            f"Zone {zone_id} is predicted to reach {predicted_peak_kw:.1f} kW, "
            f"which is {overage_pct}% {'above' if overage > 0 else 'below'} its "
            f"{capacity_kw:.1f} kW capacity limit."
        ),
    }


def analyse_stress_cause(
    zone_id: str,
    window_start: str,
    window_end: str,
    archetype_breakdown: str,
) -> dict:
    hour_start = int(window_start.split("T")[1][:2]) if "T" in window_start else 18
    causes = []
    if 16 <= hour_start <= 22:
        causes.append("evening residential peak demand (cooking, HVAC, entertainment)")
    if "wfh" in archetype_breakdown.lower():
        causes.append("high baseline load from work-from-home households")
    if "family" in archetype_breakdown.lower():
        causes.append("simultaneous appliance use by family households")
    if not causes:
        causes.append("aggregate demand spike across multiple household types")
    return {
        "primary_causes": causes,
        "stress_window": f"{window_start} to {window_end}",
        "cause_summary": " and ".join(causes).capitalize() + ".",
    }


def generate_household_nudges(households_json: str) -> dict:
    try:
        households = (
            json.loads(households_json)
            if isinstance(households_json, str)
            else households_json
        )
    except Exception:
        return {"nudges": [], "error": "invalid households JSON"}

    if not isinstance(households, list):
        return {"nudges": [], "error": "expected a list of households"}

    archetype_nudges = {
        "family": {
            "message": "Your household uses significant evening energy. Small shifts make a big impact on your zone's grid.",
            "action": "Delay dishwasher, washing machine, and dryer to after 10pm tonight.",
        },
        "single_professional": {
            "message": "As a professional household, your evening usage aligns with peak demand. Shifting it helps the whole zone.",
            "action": "Set AC to 26\u00b0C instead of 22\u00b0C between 6pm\u201310pm, and delay laundry to 11pm.",
        },
        "wfh": {
            "message": "WFH households contribute to daytime and evening load. Your cooperation has an outsized effect.",
            "action": "Pre-cool your home before 4pm, then raise thermostat by 2\u00b0C during the 6pm\u201310pm peak.",
        },
        "retired": {
            "message": "Your steady daily usage is valuable. A small evening shift helps protect the grid for everyone.",
            "action": "Avoid using the oven or high-draw appliances between 6pm and 9pm tonight.",
        },
        "small_business": {
            "message": "Commercial loads significantly impact the grid during transition hours.",
            "action": "Stagger equipment shutdown between 5pm\u20137pm and pre-cool the space before 4pm.",
        },
    }

    nudges = []
    for h in households:
        if not isinstance(h, dict):
            continue
        household_id = h.get("household_id")
        if not household_id:
            continue
        archetype = h.get("archetype", "family")
        template = archetype_nudges.get(archetype, archetype_nudges["family"])
        nudges.append(
            {
                "household_id": household_id,
                "archetype": archetype,
                "message": template["message"],
                "suggested_shift": template["action"],
            }
        )
    return {"nudges": nudges, "total": len(nudges)}


# ─── Build the ADK agent + runner ────────────────────────────────────────────


def _build_agent() -> Agent:
    return Agent(
        name="voltrix_grid_intelligence_agent",
        model="gemma-4-31b-it",
        description="VOLTRIX grid agent: stress analysis, root-cause diagnosis, citizen nudges.",
        instruction=(
            "You are a VOLTRIX grid expert. Given a stress event:\n"
            "1. get_zone_details — severity\n"
            "2. analyse_stress_cause — why\n"
            "3. generate_household_nudges — per-home actions\n"
            "4. Return ONLY valid JSON:\n"
            '{"reasoning":"why","utility_action":"operator action","household_nudges":['
            '{"household_id":"","message":"<25 words","suggested_shift":"action"}]}\n'
            "Rules: plain English; specific utility action; nudge every household; no markdown."
        ),
        tools=[
            FunctionTool(get_zone_details),
            FunctionTool(analyse_stress_cause),
            FunctionTool(generate_household_nudges),
        ],
        generate_content_config=types.GenerateContentConfig(
            temperature=0.3,
        ),
    )


_session_service = InMemorySessionService()
_agent_instance: Agent | None = None
_runner_instance: Runner | None = None


def _get_runner() -> Runner:
    global _agent_instance, _runner_instance
    if _runner_instance is None:
        _agent_instance = _build_agent()
        _runner_instance = Runner(
            agent=_agent_instance,
            app_name="voltrix",
            session_service=_session_service,
            auto_create_session=True,
        )
    return _runner_instance


async def _run_agent(
    prompt: str, user_id: str = "user", session_id: str = "default"
) -> str:
    runner = _get_runner()
    last_text = ""
    message = types.Content(parts=[types.Part(text=prompt)], role="user")
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=message,
    ):
        if event.is_final_response():
            content = event.message  # PartUnion / content object
            if hasattr(content, "parts"):
                for part in content.parts:
                    if hasattr(part, "text") and part.text:
                        last_text = part.text
            elif hasattr(content, "text") and content.text:
                last_text = content.text
            elif isinstance(content, str):
                last_text = content
    return last_text


# ─── Public functions called by main.py ──────────────────────────────────────


def run_stress_analysis(stress_event: dict, household_summaries: list[dict]) -> dict:
    prompt = f"Analyse this stress event and recommend actions.\nEvent: {json.dumps(stress_event, default=str)}\nHouseholds: {json.dumps(household_summaries, default=str)}"
    raw = ""
    try:
        raw = asyncio.run(_run_agent(prompt))
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)

        if "household_nudges" not in result:
            result["household_nudges"] = []
        if not result["household_nudges"] and household_summaries:
            # Agent skipped the nudges tool — backstop so the demo never
            # shows zero nudges for a real stress event.
            result["household_nudges"] = _fallback_response(
                stress_event, household_summaries
            )["household_nudges"]

        return result
    except json.JSONDecodeError:
        import re

        try:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass
        logger.error("Agent returned unparseable JSON — using fallback")
        return _fallback_response(stress_event, household_summaries)
    except Exception as e:
        logger.error(f"ADK agent error: {e}", exc_info=True)
        return _fallback_response(stress_event, household_summaries)


def answer_question(question: str, context: str) -> str:
    prompt = f"Q: {question}\nCtx: {context}"
    try:
        return asyncio.run(_run_agent(prompt, session_id="chat"))
    except Exception as e:
        logger.error(f"Agent chat error: {e}", exc_info=True)
        return "Sorry, I couldn't process that right now. Please try again."


# ─── Direct genai streaming (no ADK) for chat SSE ─────────────────────────

_genai_client = None


def _get_genai_client() -> genai_sdk.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai_sdk.Client(api_key=GEMINI_API_KEY)
    return _genai_client


_CHAT_SYSTEM_INSTRUCTION = (
    "You are a VOLTRIX grid expert helping users understand their smart grid "
    "data. Answer concisely using the provided context. Be specific and "
    "data-driven. If you don't know, say so. No markdown formatting."
)


async def answer_question_stream(
    question: str, context: str
) -> AsyncGenerator[str, None]:
    """Async generator that yields text tokens as they stream from Gemma.
    Uses a background thread + asyncio.Queue to bridge the sync genai SDK
    into the async event loop so FastAPI can stream each token via SSE."""
    client = _get_genai_client()
    prompt = f"Q: {question}\nCtx: {context}"
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _produce():
        try:
            response = client.models.generate_content_stream(
                model="gemma-4-31b-it",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=types.Content(
                        parts=[types.Part(text=_CHAT_SYSTEM_INSTRUCTION)]
                    ),
                    temperature=0.3,
                ),
            )
            for chunk in response:
                if hasattr(chunk, "text") and chunk.text:
                    asyncio.run_coroutine_threadsafe(
                        queue.put(chunk.text), loop
                    ).result()
        except Exception as e:
            logger.error("genai stream error: %s", e, exc_info=True)
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    loop.run_in_executor(None, _produce)

    while True:
        token = await queue.get()
        if token is None:
            break
        yield token


def _fallback_response(stress_event: dict, household_summaries: list[dict]) -> dict:
    peak = stress_event.get("predicted_peak_kw", 0)
    cap = stress_event.get("capacity_kw", 1)
    zone = stress_event.get("zone_id", "unknown")
    return {
        "reasoning": (
            f"Zone {zone} is predicted to reach {peak:.1f} kW, exceeding its "
            f"{cap:.1f} kW capacity during the evening peak. High residential demand "
            f"from simultaneous appliance use is the primary driver."
        ),
        "utility_action": (
            "Activate demand-response protocols for the affected zone. "
            "Alert high-load households to shift discretionary appliance use to after 10pm."
        ),
        "household_nudges": [
            {
                "household_id": h["household_id"],
                "message": "Your zone expects peak grid load tonight. Please delay high-energy appliances to after 10pm.",
                "suggested_shift": "Delay washing machine, dishwasher, and AC to after 10pm.",
            }
            for h in household_summaries[:6]
        ],
    }
