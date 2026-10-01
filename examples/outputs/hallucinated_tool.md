# Autopsy Report: Run run_hallucination_001

**Generated:** 2026-10-01 00:07:51

---

## Summary

- **Health Score:** 77/100
- **Confidence:** 90%

- **Status:** failed
- **Events:** 7 | **Errors (stats):** 3

Found 4 high severity issue(s), 1 medium severity issue(s). Top hypothesis: Model calling non-existent tools (hallucination) (confidence: 90%)

---

## Timeline

- [000] . message
- [001] . message
- [002] . llm_call (gpt-4)
- [003] X tool_call (email_sender)
- [004] . llm_call (gpt-4)
- [005] X tool_call (send_mail)
- [006] X error

---

## Root Cause Chain

1. Model calling non-existent tools (hallucination) (confidence: 90%)
2. Unhandled error causing cascade failures (confidence: 80%)
3. Tool input/output not matching expected schema (confidence: 70%)

---

## Fix Recommendations

### A) Graph/Code Fixes

- Add try/except blocks around tool calls
- Implement graceful error recovery
- Add fallback behavior for failed operations
- Introduce localized error handling to stop one failure from propagating.

### B) Tool Contract Fixes

- Add schema validation before tool calls
- Update tool schemas to match actual behavior
- Add type coercion for common mismatches
- Add output validation on tool results
- Handle null responses gracefully
- Add retry logic for empty responses
- Validate tool/LLM outputs and retry with bounded fallback on empty results.

### C) Prompt/Policy Fixes

- Add stricter tool definitions in system prompt
- Validate tool names before execution
- Use structured output for tool selection
- Constrain tool use to declared tool names and validate before dispatch.

---

## Evidence

**Cited Events:** [3, 5, 6]

---

## Trace Statistics

- Total Events: 7
- LLM Calls: 2
- Tool Calls: 2
- Errors: 3
- Total Tokens: 250
- Duration: 1100 ms

---

### Empty Response (medium)
- **What:** An LLM or tool returned an empty or effectively empty response.
- **Where (event IDs):** 3, 5
**Evidence (trace excerpts)**
**Event 3**
```
type=tool_call | name=email_sender
{"input": {"to": "john@example.com", "subject": "Meeting Notes", "body": "Here are the meeting notes..."}, "output": null, "error": "message=\"Tool 'email_sender' not found\" stack=None category='ToolNotFoundError'"}
```
**Event 5**
```
type=tool_call | name=send_mail
{"input": {"recipient": "john@example.com", "message": "Meeting notes..."}, "output": null, "error": "message=\"Tool 'send_mail' not found\" stack=None category='ToolNotFoundError'"}
```
**Likely cause (heuristic):** Model/tool returned nothing; check temperature, truncation, or API errors.
---
### Error Cascade (high)
- **What:** Multiple errors chained; later steps failed because earlier ones did.
- **Where (event IDs):** 3, 5, 6
**Evidence (trace excerpts)**
**Event 3**
```
type=tool_call | name=email_sender
{"input": {"to": "john@example.com", "subject": "Meeting Notes", "body": "Here are the meeting notes..."}, "output": null, "error": "message=\"Tool 'email_sender' not found\" stack=None category='ToolNotFoundError'"}
```
**Event 5**
```
type=tool_call | name=send_mail
{"input": {"recipient": "john@example.com", "message": "Meeting notes..."}, "output": null, "error": "message=\"Tool 'send_mail' not found\" stack=None category='ToolNotFoundError'"}
```
**Event 6**
```
type=error
{"input": null, "output": null, "error": "message='Unable to complete task - required tool not available' stack=None category='TaskFailedError'"}
```
**Likely cause (heuristic):** Insufficient isolation; one error poisons downstream steps.
---
### Hallucinated Tool (high)
- **What:** The agent called a tool name that is not in the declared tool set.
- **Where (event IDs):** 3, 5
**Evidence (trace excerpts)**
**Event 3**
```
type=tool_call | name=email_sender
{"input": {"to": "john@example.com", "subject": "Meeting Notes", "body": "Here are the meeting notes..."}, "output": null, "error": "message=\"Tool 'email_sender' not found\" stack=None category='ToolNotFoundError'"}
```
**Event 5**
```
type=tool_call | name=send_mail
{"input": {"recipient": "john@example.com", "message": "Meeting notes..."}, "output": null, "error": "message=\"Tool 'send_mail' not found\" stack=None category='ToolNotFoundError'"}
```
**Likely cause (heuristic):** Prompt allows free-form tools or schema drift vs runtime registry.
---
### Contract Unknown Tool (high)
- **What:** Pattern detected by static analysis of the trace.
- **Where (event IDs):** 3
- **Note:** Same event as the Hallucinated Tool finding above; the health score counts each event once.
**Evidence (trace excerpts)**
**Event 3**
```
type=tool_call | name=email_sender
{"input": {"to": "john@example.com", "subject": "Meeting Notes", "body": "Here are the meeting notes..."}, "output": null, "error": "message=\"Tool 'email_sender' not found\" stack=None category='ToolNotFoundError'"}
```
**Likely cause (heuristic):** Review the cited events and surrounding tool/LLM steps (heuristic).
---
### Contract Unknown Tool (high)
- **What:** Pattern detected by static analysis of the trace.
- **Where (event IDs):** 5
- **Note:** Same event as the Hallucinated Tool finding above; the health score counts each event once.
**Evidence (trace excerpts)**
**Event 5**
```
type=tool_call | name=send_mail
{"input": {"recipient": "john@example.com", "message": "Meeting notes..."}, "output": null, "error": "message=\"Tool 'send_mail' not found\" stack=None category='ToolNotFoundError'"}
```
**Likely cause (heuristic):** Review the cited events and surrounding tool/LLM steps (heuristic).
---