"""
Tool contracts and validation schemas.

This module defines schemas for validating tool inputs/outputs
and detecting contract violations.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ViolationSeverity(str, Enum):
    """Severity level for contract violations."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ToolContract(BaseModel):
    """
    Contract definition for a tool.

    Defines expected input/output schemas and validation rules.
    """
    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None  # JSON Schema
    output_schema: dict[str, Any] | None = None  # JSON Schema
    required_metadata: list[str] = Field(default_factory=list)
    max_retries: int | None = None
    timeout_ms: int | None = None


class ContractViolation(BaseModel):
    """
    A detected contract violation.

    Contains the event ID, violation type, and suggested fix.
    """
    event_id: int
    tool_name: str
    violation_type: str  # unknown_tool, invalid_input, invalid_output, missing_metadata
    severity: ViolationSeverity
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    suggested_fix: str | None = None
    # Informational violations are reported as notes, not scored or gated.
    informational: bool = False


class ContractRegistry(BaseModel):
    """
    Registry of tool contracts.

    Used to validate tool calls against expected schemas.
    """
    tools: dict[str, ToolContract] = Field(default_factory=dict)

    def register_tool(self, contract: ToolContract) -> None:
        """Register a tool contract."""
        self.tools[contract.name] = contract

    def get_contract(self, tool_name: str) -> ToolContract | None:
        """Get contract for a tool."""
        return self.tools.get(tool_name)

    def is_known_tool(self, tool_name: str) -> bool:
        """Check if a tool is registered."""
        return tool_name in self.tools

    def get_all_tool_names(self) -> list[str]:
        """Get list of all registered tool names."""
        return list(self.tools.keys())
