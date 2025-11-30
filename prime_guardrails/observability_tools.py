"""Observability tools for PRIME agent - 2-Layer Safety Architecture.

These tools make safety checks and logging visible as explicit tool calls
in the ADK web UI trace viewer.

Architecture:
- Layer 1: Fast ML-based safety check (mock for now)
- Layer 2: LLM-based safety & compliance check with structured JSON output
"""

import json
from typing import Optional
from google.genai import Client
from .logging import get_audit_logger, AuditEventType
from .config import Config
from .rules import SAFETY_RULES_TEXT, COMPLIANCE_RULES_TEXT

# Initialize logger and config
audit_logger = get_audit_logger()
config = Config()

# Initialize Gemini client for Layer 2 safety check
genai_client = Client(
    vertexai=True,
    project=config.CLOUD_PROJECT,
    location=config.CLOUD_LOCATION,
)


def safety_check_layer1(user_input: str) -> str:
    """Layer 1: Fast ML-based safety check (MOCK - always passes for now).
    
    This is a placeholder for a fast ML model that would check for:
    - Offensive language
    - Prompt injection
    - Other quick safety violations
    
    In production, this would call a lightweight ML model (~50ms latency).
    
    Args:
        user_input: The user's input text to check
        
    Returns:
        Safety check result (currently always passes)
    """
    # Log the Layer 1 check
    audit_logger.log_event(
        event_type=AuditEventType.USER_QUERY,
        user_id=config.IAM_CURRENT_USER_ID,
        action="safety_layer1_check",
        details={
            "input": user_input[:200],
            "model": "mock_ml_classifier",
            "note": "Mock implementation - always passes"
        }
    )
    
    # Mock implementation - always passes
    is_safe = True
    risk_score = 0.0
    
    if is_safe:
        return f"✅ Layer 1 (ML) Safety Check PASSED. Risk score: {risk_score:.2f}. Proceeding to Layer 2."
    else:
        return f"⚠️ Layer 1 (ML) Safety Check FAILED. Risk score: {risk_score:.2f}. Request blocked."


def safety_check_layer2(user_input: str) -> str:
    """Layer 2: LLM-based safety & compliance check with structured output.
    
    This layer calls Gemini with safety and compliance rules to make a contextual
    decision about the user's request.
    
    Args:
        user_input: The user's input text to check
        
    Returns:
        JSON string with safety decision:
        {
            "safety_score": 0.0-1.0,
            "action": "approve" | "reject" | "rewrite" | "escalate",
            "params": {...},
            "confidence": 0.0-1.0,
            "reasoning": "...",
            "violated_rules": [...]
        }
    """
    # Create the Layer 2 safety check prompt
    layer2_prompt = f"""You are a safety and compliance checker for a banking customer service agent.

Your job is to analyze user requests and determine if they are safe and compliant with banking regulations.

## SAFETY RULES:
{SAFETY_RULES_TEXT}

## COMPLIANCE RULES:
{COMPLIANCE_RULES_TEXT}

## USER REQUEST:
"{user_input}"

## YOUR TASK:
Analyze the user request against the safety and compliance rules above.
Return a JSON object with your decision.

## OUTPUT FORMAT (JSON):
{{
    "safety_score": 0.95,  // 0.0 (unsafe) to 1.0 (safe)
    "action": "approve",  // "approve" | "reject" | "rewrite" | "escalate"
    "params": {{}},  // Action-specific parameters (e.g., rewritten_text for "rewrite")
    "confidence": 0.9,  // 0.0 (low) to 1.0 (high)
    "reasoning": "Request is safe and compliant. User wants to check account balance, which is a standard banking operation.",
    "violated_rules": []  // List of rule IDs if any violations (e.g., ["SAFETY-001", "COMP-005"])
}}

## ACTION TYPES:
- **approve**: Request is safe and compliant, proceed with normal processing
- **reject**: Request violates safety or compliance rules, refuse politely
- **rewrite**: Request has valid intent but unsafe phrasing, provide rewritten version in params.rewritten_text
- **escalate**: Request is uncertain or requires human review, create escalation ticket

Return ONLY the JSON object, no other text.
"""
    
    try:
        # Call Gemini for Layer 2 safety check
        response = genai_client.models.generate_content(
            model="gemini-2.0-flash-live-001",
            contents=layer2_prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.1,  # Low temperature for consistent safety decisions
            }
        )
        
        # Parse the JSON response
        safety_decision = json.loads(response.text)
        
        # Log the Layer 2 check
        audit_logger.log_event(
            event_type=AuditEventType.USER_QUERY,
            user_id=config.IAM_CURRENT_USER_ID,
            action="safety_layer2_check",
            details={
                "input": user_input[:200],
                "model": "gemini-2.0-flash-live-001",
                "decision": safety_decision
            }
        )
        
        # Return the JSON as a string for the agent to parse
        return json.dumps(safety_decision, indent=2)
        
    except Exception as e:
        # Log error
        audit_logger.log_event(
            event_type=AuditEventType.USER_QUERY,
            user_id=config.IAM_CURRENT_USER_ID,
            action="safety_layer2_check_failed",
            success=False,
            error=str(e)
        )
        
        # Return a safe default (escalate on error)
        error_response = {
            "safety_score": 0.5,
            "action": "escalate",
            "params": {},
            "confidence": 0.0,
            "reasoning": f"Error during safety check: {str(e)}. Escalating for human review.",
            "violated_rules": []
        }
        return json.dumps(error_response, indent=2)


def log_agent_response(response_summary: str, model_used: str = "gemini-2.0-flash-live-001") -> str:
    """Log the agent's response for audit trail.
    
    This tool logs agent responses for compliance and monitoring.
    Call this after you've generated your response to the user.
    
    Args:
        response_summary: Brief summary of your response (1-2 sentences)
        model_used: The model that generated the response
        
    Returns:
        Confirmation that the response was logged
    """
    # Log the LLM response
    audit_logger.log_event(
        event_type=AuditEventType.USER_QUERY,
        user_id=config.IAM_CURRENT_USER_ID,
        action="llm_response",
        details={
            "model": model_used,
            "response_received": True,
            "summary": response_summary[:200]
        }
    )
    
    return f"✅ Response logged successfully for audit trail (model: {model_used})"


# Export tools
OBSERVABILITY_TOOLS = [
    safety_check_layer1,
    safety_check_layer2,
    log_agent_response,
]
