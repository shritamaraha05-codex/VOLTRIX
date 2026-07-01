"""
reasoning.py — Gemini reasoning + nudge generation
Primary owner: Debjyoti (owns the prompt + Gemini wiring)
Wiring owner:  Mrinmoy (calls generate_reasoning_and_nudges from main.py)

Uses Google AI Studio (FREE — no billing needed).
Get your API key at: https://aistudio.google.com/app/apikey

Env required:
    GEMINI_API_KEY    from aistudio.google.com (free, no credit card)

Mrinmoy: do NOT change the function signature.
         The return shape is what db.save_stress_event / db.save_recommendation depend on.
"""

import os
import json
import re
import google.generativeai as genai

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MODEL_NAME     = "gemini-2.0-flash"   # free via AI Studio

_model: genai.GenerativeModel | None = None


def _get_model() -> genai.GenerativeModel:
    global _model
    if _model is None:
        genai.configure(api_key=GEMINI_API_KEY)
        _model = genai.GenerativeModel(MODEL_NAME)
    return _model


def generate_reasoning_and_nudges(
    stress_event: dict,
    household_summaries: list[dict],
) -> dict:
    """
    Calls Gemini with the stress event context and returns a dict:
    {
      "reasoning":        str,  -- plain-language explanation of the stress event
      "utility_action":   str,  -- 1-2 sentence grid operator action
      "household_nudges": [     -- one per household in household_summaries
        {
          "household_id":    str,
          "message":         str,  -- personalized citizen nudge
          "suggested_shift": str   -- concrete action e.g. "delay laundry to 11pm"
        }
      ]
    }

    Falls back to a safe default dict if Gemini returns malformed JSON,
    so the live demo never crashes on a bad response.
    """
    model = _get_model()

    prompt = f"""
You are a smart-grid decision-support assistant. A predicted energy stress event has been detected.
Explain why it is happening in plain language and generate personalized, actionable recommendations.

Zone: {stress_event['zone_id']}
Predicted peak load: {stress_event['predicted_peak_kw']:.2f} kW
Zone capacity: {stress_event['capacity_kw']:.2f} kW
Excess above capacity: {(stress_event['predicted_peak_kw'] - stress_event['capacity_kw']):.2f} kW
Stress window: {stress_event['window_start']} to {stress_event['window_end']}
Severity: {stress_event['severity']}

Top contributing households (sorted by average load, highest first):
{json.dumps(household_summaries, indent=2, default=str)}

Return ONLY valid JSON with no markdown fences, no preamble, no trailing text:
{{
  "reasoning": "2-3 sentence plain-language explanation of why this stress event is occurring",
  "utility_action": "1-2 sentence recommended action for the grid operator",
  "household_nudges": [
    {{
      "household_id": "<household_id from above>",
      "message": "personalized, specific nudge under 30 words",
      "suggested_shift": "concrete action e.g. delay AC/laundry/dishwasher to 11pm"
    }}
  ]
}}
"""

    response = model.generate_content(prompt)
    raw_text = response.text.strip()

    # Strip markdown fences if Gemini wraps anyway
    raw_text = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback: extract the outermost JSON object with regex
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

    # Safe fallback — demo never crashes
    return {
        "reasoning": (
            f"Predicted load of {stress_event['predicted_peak_kw']:.1f} kW exceeds zone "
            f"capacity of {stress_event['capacity_kw']:.1f} kW during the evening peak window."
        ),
        "utility_action": (
            "Consider activating demand-response protocols and alerting high-load households "
            "to shift discretionary appliance use outside the stress window."
        ),
        "household_nudges": [
            {
                "household_id": h["household_id"],
                "message": (
                    "Your zone's grid expects high demand tonight. Please delay high-energy "
                    "appliances (washing machine, dishwasher, AC) to after 10pm."
                ),
                "suggested_shift": "Delay appliances to after 10pm",
            }
            for h in household_summaries[:5]
        ],
    }
