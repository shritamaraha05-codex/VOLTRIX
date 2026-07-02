"""
agent.py — VOLTRIX Grid Intelligence Agent (Google ADK)
Uses ADK + Gemini 2.0 Flash via AI Studio (free).

The agent has four tools it can call autonomously:
  - get_zone_details      -> fetch zone capacity and name
  - analyse_stress_cause  -> synthesise cause explanation
  - generate_household_nudges -> produce per-household recommendations

Exported functions (called by main.py):
  run_stress_analysis(stress_event, household_summaries) -> dict
  answer_question(question, context) -> str
"""

import os
import json
import logging
import google.generativeai as genai
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

logger = logging.getLogger("voltrix.agent")

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_API_KEY)


# ─── Tool functions (the agent calls these autonomously) ─────────────────────


def get_zone_details(
    zone_id: str, predicted_peak_kw: float, capacity_kw: float
) -> dict:
    """
    Returns a structured summary of the zone's stress situation.
    The agent calls this first to understand context.
    """
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
    """
    Analyses the likely causes of the stress event based on timing and archetypes.
    Returns structured cause analysis.
    """
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
    """
    Generates specific, personalised nudges for each household based on archetype.
    Returns list of nudge objects.
    """
    try:
        households = json.loads(households_json)
    except Exception:
        return {"nudges": [], "error": "invalid households JSON"}

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
        archetype = h.get("archetype", "family")
        template = archetype_nudges.get(archetype, archetype_nudges["family"])
        nudges.append(
            {
                "household_id": h["household_id"],
                "archetype": archetype,
                "message": template["message"],
                "suggested_shift": template["action"],
            }
        )

    return {"nudges": nudges, "total": len(nudges)}


# ─── Build the ADK agent ──────────────────────────────────────────────────────


def _build_agent() -> Agent:
    return Agent(
        name="voltrix_grid_intelligence_agent",
        model="gemini-2.0-flash",
        description=(
            "VOLTRIX grid intelligence agent. Analyses energy stress events, "
            "determines root causes, and generates actionable recommendations "
            "for citizens and utility operators."
        ),
        instruction="""
You are the VOLTRIX Grid Intelligence Agent — an expert AI system for energy grid management.

When analysing a stress event:
1. Call get_zone_details to understand the severity of the situation
2. Call analyse_stress_cause to determine why the stress is happening
3. Call generate_household_nudges to create personalised citizen recommendations
4. Synthesise everything into a final structured JSON response

Your final response MUST be valid JSON with this exact structure:
{
  "reasoning": "2-3 sentence plain-language explanation of why this stress event is occurring and what drives it",
  "utility_action": "1-2 sentence concrete action for the grid operator to take immediately",
  "household_nudges": [
    {
      "household_id": "...",
      "message": "personalised nudge message under 25 words",
      "suggested_shift": "specific action e.g. delay AC to 10pm"
    }
  ]
}

Rules:
- reasoning must be plain English, no jargon, readable by a citizen
- utility_action must be specific and immediately actionable
- every household in the input must get a nudge
- return ONLY the JSON object, no markdown fences, no preamble
""",
        tools=[
            FunctionTool(get_zone_details),
            FunctionTool(analyse_stress_cause),
            FunctionTool(generate_household_nudges),
        ],
    )


_agent_instance: Agent | None = None


def _get_agent() -> Agent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = _build_agent()
    return _agent_instance


# ─── Public functions called by main.py ──────────────────────────────────────


def run_stress_analysis(stress_event: dict, household_summaries: list[dict]) -> dict:
    """
    Runs the ADK agent to analyse a stress event and generate recommendations.

    Args:
        stress_event: dict from forecasting.detect_stress()
        household_summaries: list from bq.get_household_load_for_zone()

    Returns dict with keys: reasoning, utility_action, household_nudges
    Never raises — returns safe fallback on any error.
    """
    agent = _get_agent()

    prompt = f"""
Analyse this energy stress event and generate recommendations.

Stress event data:
{json.dumps(stress_event, indent=2, default=str)}

Household data (top contributors by load):
{json.dumps(household_summaries, indent=2, default=str)}

Use your tools to analyse the situation, then return the structured JSON response.
"""

    raw = ""
    try:
        response = agent.run(prompt)
        # Extract text from ADK response
        raw = response.text.strip() if hasattr(response, "text") else str(response)
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)

        # Ensure household_nudges has correct structure
        if "household_nudges" not in result:
            result["household_nudges"] = []

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
    """
    Answers a citizen or operator question using the agent with context.
    Used by the /chat endpoint.
    """
    agent = _get_agent()
    prompt = f"""
Answer the following question about the VOLTRIX energy platform.
Use only the context provided. Be concise, friendly, and specific.
Do not mention that you are an AI.

Question: {question}

Context:
{context}
"""
    try:
        response = agent.run(prompt)
        return response.text.strip() if hasattr(response, "text") else str(response)
    except Exception as e:
        logger.error(f"Agent chat error: {e}", exc_info=True)
        return "Sorry, I couldn't process that right now. Please try again."


def _fallback_response(stress_event: dict, household_summaries: list[dict]) -> dict:
    """Safe fallback if agent fails — never crashes the demo."""
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
