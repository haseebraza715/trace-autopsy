"""
Contract validation module.

Validates tool usage against defined contracts:
- Tool existence in allow-list
- Input schema validation
- Output schema validation
- Required metadata presence
"""

from dataclasses import dataclass, field
from typing import Any

from agent_autopsy.schema import EventType, Trace, TraceEvent
from agent_autopsy.schema.contracts import (
    ContractRegistry,
    ContractViolation,
    ToolContract,
    ViolationSeverity,
)


@dataclass
class ContractValidationResult:
    """Result of contract validation."""
    violations: list[ContractViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0


class ContractValidator:
    """
    Validates tool calls against defined contracts.

    Performs deterministic validation without LLM.
    """

    def __init__(self, trace: Trace, registry: ContractRegistry | None = None):
        self.trace = trace
        self.registry = registry or ContractRegistry()

        # Auto-register tools from trace environment
        for tool_name in trace.env.tools_available:
            if not self.registry.is_known_tool(tool_name):
                self.registry.register_tool(ToolContract(name=tool_name))

    def validate_all(self) -> ContractValidationResult:
        """Run all contract validations."""
        result = ContractValidationResult()

        for event in self.trace.get_tool_calls():
            violations = self._validate_tool_call(event)
            result.violations.extend(violations)

        return result

    def _validate_tool_call(self, event: TraceEvent) -> list[ContractViolation]:
        """Validate a single tool call event."""
        violations = []

        if not event.name:
            violations.append(
                ContractViolation(
                    event_id=event.event_id,
                    tool_name="<unknown>",
                    violation_type="missing_tool_name",
                    severity=ViolationSeverity.HIGH,
                    message="Tool call without tool name",
                    suggested_fix="Ensure tool name is specified in the call",
                )
            )
            return violations

        # Check if tool is known - but only if tools_available was provided
        # Skip unknown-tool validation when no allowlist is available
        # (e.g., traces from TraceSaver that don't include tool metadata)
        if self.trace.env.tools_available:
            if not self.registry.is_known_tool(event.name):
                violations.append(
                    ContractViolation(
                        event_id=event.event_id,
                        tool_name=event.name,
                        violation_type="unknown_tool",
                        severity=ViolationSeverity.HIGH,
                        message=f"Tool '{event.name}' not in allow-list",
                        evidence={"available_tools": self.registry.get_all_tool_names()},
                        suggested_fix=f"Add '{event.name}' to available tools or use existing tool",
                    )
                )
                return violations

        contract = self.registry.get_contract(event.name)

        if contract:
            # Validate input schema
            if contract.input_schema:
                input_violations = self._validate_schema(
                    event.input,
                    contract.input_schema,
                    event.event_id,
                    event.name,
                    "input",
                )
                violations.extend(input_violations)

            # Validate output schema
            if contract.output_schema and event.output:
                output_violations = self._validate_schema(
                    event.output,
                    contract.output_schema,
                    event.event_id,
                    event.name,
                    "output",
                )
                violations.extend(output_violations)

            # Check required metadata
            for required_field in contract.required_metadata:
                if required_field not in event.metadata:
                    violations.append(
                        ContractViolation(
                            event_id=event.event_id,
                            tool_name=event.name,
                            violation_type="missing_metadata",
                            severity=ViolationSeverity.MEDIUM,
                            message=f"Missing required metadata: {required_field}",
                            suggested_fix=f"Add '{required_field}' to tool call metadata",
                        )
                    )

        # Check for missing common metadata
        missing_metadata = []
        if event.latency_ms is None:
            missing_metadata.append("latency_ms")
        if event.token_count is None and event.type == EventType.LLM_CALL:
            missing_metadata.append("token_count")

        if missing_metadata:
            violations.append(
                ContractViolation(
                    event_id=event.event_id,
                    tool_name=event.name,
                    violation_type="missing_metadata",
                    severity=ViolationSeverity.LOW,
                    message=f"Missing optional metadata: {', '.join(missing_metadata)}",
                    suggested_fix="Track latency and token counts for better analysis",
                    informational=True,
                )
            )

        return violations

    def _validate_schema(
        self,
        data: Any,
        schema: dict,
        event_id: int,
        tool_name: str,
        field_type: str,
    ) -> list[ContractViolation]:
        """
        Validate data against a JSON schema.

        This is a simplified validation - full JSON Schema validation
        would require jsonschema library.
        """
        violations = []

        if not isinstance(schema, dict):
            return violations

        # Check required fields
        required = schema.get("required", [])
        if isinstance(data, dict):
            for req_field in required:
                if req_field not in data:
                    violations.append(
                        ContractViolation(
                            event_id=event_id,
                            tool_name=tool_name,
                            violation_type=f"invalid_{field_type}",
                            severity=ViolationSeverity.HIGH,
                            message=f"Missing required {field_type} field: {req_field}",
                            evidence={"missing_field": req_field, "schema": schema},
                            suggested_fix=f"Include '{req_field}' in tool {field_type}",
                        )
                    )

        # Check type
        expected_type = schema.get("type")
        if expected_type:
            type_map = {
                "object": dict,
                "array": list,
                "string": str,
                "number": (int, float),
                "integer": int,
                "boolean": bool,
            }
            expected = type_map.get(expected_type)
            if expected and data is not None and not isinstance(data, expected):
                violations.append(
                    ContractViolation(
                        event_id=event_id,
                        tool_name=tool_name,
                        violation_type=f"invalid_{field_type}",
                        severity=ViolationSeverity.HIGH,
                        message=f"Invalid {field_type} type: expected {expected_type}, got {type(data).__name__}",
                        evidence={"expected": expected_type, "actual": type(data).__name__},
                        suggested_fix=f"Ensure {field_type} is of type {expected_type}",
                    )
                )

        return violations

    def get_violations(self) -> list[ContractViolation]:
        """Get all contract violations."""
        return self.validate_all().violations
