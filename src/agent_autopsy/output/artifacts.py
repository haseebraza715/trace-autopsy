"""
Artifact generation for Agent Autopsy.

Generates patch artifacts:
- Patched system prompts
- Retry policy code snippets
- Router logic patches
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_autopsy.preanalysis import PreAnalysisBundle
from agent_autopsy.schema import Trace
from agent_autopsy.utils.atomic import aliases_source, atomic_write_text


@dataclass
class Artifact:
    """A generated artifact."""
    name: str
    content: str
    artifact_type: str  # prompt, code, config
    description: str


class ArtifactGenerator:
    """
    Generates fix artifacts from analysis results.

    Creates actionable patches that can be applied to fix issues.
    """

    def __init__(self, trace: Trace, preanalysis: PreAnalysisBundle):
        self.trace = trace
        self.preanalysis = preanalysis

    def generate_all(self) -> list[Artifact]:
        """Generate all applicable artifacts."""
        artifacts = []

        for hypothesis in self.preanalysis.hypotheses:
            if hypothesis.category == "prompt":
                artifacts.extend(self._generate_prompt_artifacts(hypothesis))
            elif hypothesis.category == "code":
                artifacts.extend(self._generate_code_artifacts(hypothesis))
            elif hypothesis.category == "ops":
                artifacts.extend(self._generate_ops_artifacts(hypothesis))
            elif hypothesis.category == "tool":
                artifacts.extend(self._generate_tool_artifacts(hypothesis))

        return artifacts

    def _generate_prompt_artifacts(self, hypothesis: Any) -> list[Artifact]:
        """Generate prompt-related artifacts."""
        artifacts = []

        if "hallucin" in hypothesis.description.lower():
            artifacts.append(
                Artifact(
                    name="tool_guardrail_prompt.txt",
                    content=self._generate_tool_guardrail_prompt(),
                    artifact_type="prompt",
                    description="System prompt addition to prevent tool hallucination",
                )
            )

        if "loop" in hypothesis.description.lower():
            artifacts.append(
                Artifact(
                    name="loop_prevention_prompt.txt",
                    content=self._generate_loop_prevention_prompt(),
                    artifact_type="prompt",
                    description="System prompt addition to prevent infinite loops",
                )
            )

        return artifacts

    def _generate_code_artifacts(self, hypothesis: Any) -> list[Artifact]:
        """Generate code-related artifacts."""
        artifacts = []

        if "loop" in hypothesis.description.lower() or "exit" in hypothesis.description.lower():
            artifacts.append(
                Artifact(
                    name="loop_guard.py",
                    content=self._generate_loop_guard_code(),
                    artifact_type="code",
                    description="Loop detection and prevention guard",
                )
            )

        if "error" in hypothesis.description.lower() or "cascade" in hypothesis.description.lower():
            artifacts.append(
                Artifact(
                    name="error_handler.py",
                    content=self._generate_error_handler_code(),
                    artifact_type="code",
                    description="Error handling wrapper for tool calls",
                )
            )

        return artifacts

    def _generate_ops_artifacts(self, hypothesis: Any) -> list[Artifact]:
        """Generate ops-related artifacts."""
        artifacts = []

        if "retry" in hypothesis.description.lower():
            artifacts.append(
                Artifact(
                    name="retry_policy.py",
                    content=self._generate_retry_policy_code(),
                    artifact_type="code",
                    description="Exponential backoff retry policy",
                )
            )

        if "overflow" in hypothesis.description.lower() or "context" in hypothesis.description.lower():
            artifacts.append(
                Artifact(
                    name="context_manager.py",
                    content=self._generate_context_manager_code(),
                    artifact_type="code",
                    description="Context window management utilities",
                )
            )

        return artifacts

    def _generate_tool_artifacts(self, hypothesis: Any) -> list[Artifact]:
        """Generate tool-related artifacts."""
        artifacts = []

        if "schema" in hypothesis.description.lower() or "contract" in hypothesis.description.lower():
            artifacts.append(
                Artifact(
                    name="tool_validator.py",
                    content=self._generate_tool_validator_code(),
                    artifact_type="code",
                    description="Tool input/output schema validator",
                )
            )

        return artifacts

    def _generate_tool_guardrail_prompt(self) -> str:
        """Generate prompt for tool guardrails."""
        available_tools = self.trace.env.tools_available
        tools_list = "\n".join(f"- {t}" for t in available_tools) if available_tools else "- (no tools defined)"

        return f'''## Tool Usage Guidelines

You have access to the following tools ONLY:
{tools_list}

IMPORTANT RULES:
1. ONLY call tools from the list above
2. Do NOT invent or hallucinate tool names
3. If you need functionality not provided by available tools, explain what you need and ask for guidance
4. Always validate tool inputs match the expected schema before calling
5. Handle tool errors gracefully and do not retry more than 3 times

If a tool call fails, analyze the error before retrying with the same inputs.
'''

    def _generate_loop_prevention_prompt(self) -> str:
        """Generate prompt for loop prevention."""
        return '''## Loop Prevention Guidelines

CRITICAL: Avoid infinite loops by following these rules:

1. **Track your actions**: Keep mental note of actions you've taken
2. **Detect repetition**: If you're about to do the same action with the same inputs for the 3rd time, STOP
3. **Change strategy**: If an approach isn't working after 2 attempts, try a different approach
4. **Ask for help**: If stuck in a loop, explain the situation and ask the user for guidance
5. **Set limits**: No more than 5 retries for any single operation

Signs you may be in a loop:
- Same tool call with identical inputs
- Same error repeated
- No progress toward the goal

When you detect a potential loop, IMMEDIATELY:
1. Stop the current action
2. Explain what's happening
3. Propose alternative approaches
'''

    def _generate_loop_guard_code(self) -> str:
        """Generate loop guard code."""
        return '''"""Loop detection and prevention guard."""

from collections import defaultdict
from typing import Any, Callable
import hashlib


class LoopGuard:
    """Detects and prevents infinite loops in agent execution."""

    def __init__(self, max_repetitions: int = 3):
        self.max_repetitions = max_repetitions
        self.call_history: dict[str, int] = defaultdict(int)

    def get_call_signature(self, tool_name: str, args: dict[str, Any]) -> str:
        """Generate a signature for a tool call."""
        args_str = str(sorted(args.items()))
        return f"{tool_name}:{hashlib.md5(args_str.encode()).hexdigest()}"

    def should_allow(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Check if this call should be allowed."""
        signature = self.get_call_signature(tool_name, args)
        self.call_history[signature] += 1
        return self.call_history[signature] <= self.max_repetitions

    def get_repetition_count(self, tool_name: str, args: dict[str, Any]) -> int:
        """Get how many times this exact call has been made."""
        signature = self.get_call_signature(tool_name, args)
        return self.call_history[signature]

    def reset(self):
        """Reset the call history."""
        self.call_history.clear()

    def wrap_tool(self, tool_fn: Callable) -> Callable:
        """Wrap a tool function with loop detection."""
        def wrapped(tool_name: str, args: dict[str, Any]) -> Any:
            if not self.should_allow(tool_name, args):
                count = self.get_repetition_count(tool_name, args)
                raise LoopDetectedError(
                    f"Tool '{tool_name}' called {count} times with same args. "
                    "Potential infinite loop detected."
                )
            return tool_fn(tool_name, args)
        return wrapped


class LoopDetectedError(Exception):
    """Raised when a potential infinite loop is detected."""
    pass


# Usage example:
# guard = LoopGuard(max_repetitions=3)
#
# @guard.wrap_tool
# def execute_tool(tool_name: str, args: dict) -> Any:
#     # Your tool execution logic
#     pass
'''

    def _generate_error_handler_code(self) -> str:
        """Generate error handler code."""
        return '''"""Error handling wrapper for tool calls."""

from typing import Any, Callable, TypeVar
from dataclasses import dataclass
import traceback

T = TypeVar("T")


@dataclass
class ToolResult:
    """Result of a tool call."""
    success: bool
    result: Any = None
    error: str | None = None
    error_type: str | None = None


def safe_tool_call(
    tool_fn: Callable[..., T],
    *args,
    fallback: T | None = None,
    **kwargs
) -> ToolResult:
    """
    Safely execute a tool call with error handling.

    Args:
        tool_fn: The tool function to call
        *args: Arguments to pass to the tool
        fallback: Optional fallback value on error
        **kwargs: Keyword arguments to pass to the tool

    Returns:
        ToolResult with success status and result or error
    """
    try:
        result = tool_fn(*args, **kwargs)
        return ToolResult(success=True, result=result)
    except Exception as e:
        return ToolResult(
            success=False,
            result=fallback,
            error=str(e),
            error_type=type(e).__name__,
        )


class ErrorRecoveryHandler:
    """Handles errors with recovery strategies."""

    def __init__(self):
        self.error_count = 0
        self.max_errors = 5

    def handle_error(self, error: Exception, context: dict[str, Any]) -> str:
        """Handle an error and return recovery suggestion."""
        self.error_count += 1

        if self.error_count >= self.max_errors:
            return "MAX_ERRORS_REACHED"

        error_type = type(error).__name__

        # Suggest recovery based on error type
        if "Timeout" in error_type:
            return "RETRY_WITH_BACKOFF"
        elif "RateLimit" in error_type:
            return "WAIT_AND_RETRY"
        elif "NotFound" in error_type:
            return "SKIP_AND_CONTINUE"
        elif "Validation" in error_type:
            return "FIX_INPUT"
        else:
            return "LOG_AND_CONTINUE"

    def should_abort(self) -> bool:
        """Check if we should abort due to too many errors."""
        return self.error_count >= self.max_errors


# Usage example:
# result = safe_tool_call(my_tool, arg1, arg2, fallback=default_value)
# if not result.success:
#     print(f"Tool failed: {result.error}")
'''

    def _generate_retry_policy_code(self) -> str:
        """Generate retry policy code."""
        return '''"""Exponential backoff retry policy."""

import asyncio
import time
from typing import Any, Callable, TypeVar
from functools import wraps

T = TypeVar("T")


class RetryPolicy:
    """Configurable retry policy with exponential backoff."""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for the given attempt number."""
        delay = self.base_delay * (self.exponential_base ** attempt)
        return min(delay, self.max_delay)

    def retry(self, func: Callable[..., T]) -> Callable[..., T]:
        """Decorator to retry a function with exponential backoff."""
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            for attempt in range(self.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < self.max_retries:
                        delay = self.get_delay(attempt)
                        print(f"Attempt {attempt + 1} failed, retrying in {delay:.1f}s...")
                        time.sleep(delay)
            raise last_exception
        return wrapper

    def retry_async(self, func: Callable[..., T]) -> Callable[..., T]:
        """Async decorator to retry a function with exponential backoff."""
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            for attempt in range(self.max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < self.max_retries:
                        delay = self.get_delay(attempt)
                        print(f"Attempt {attempt + 1} failed, retrying in {delay:.1f}s...")
                        await asyncio.sleep(delay)
            raise last_exception
        return wrapper


# Default policy instance
default_policy = RetryPolicy()


# Usage example:
# @default_policy.retry
# def flaky_api_call():
#     # Your API call here
#     pass
'''

    def _generate_context_manager_code(self) -> str:
        """Generate context window management code."""
        return '''"""Context window management utilities."""

from typing import Any
from dataclasses import dataclass


@dataclass
class Message:
    """A message in the conversation."""
    role: str
    content: str
    token_count: int = 0


class ContextManager:
    """Manages conversation context to prevent overflow."""

    def __init__(self, max_tokens: int = 100000, buffer_tokens: int = 4000):
        self.max_tokens = max_tokens
        self.buffer_tokens = buffer_tokens
        self.messages: list[Message] = []
        self.total_tokens = 0

    def add_message(self, message: Message) -> None:
        """Add a message, trimming old messages if needed."""
        self.messages.append(message)
        self.total_tokens += message.token_count

        # Trim if over limit
        while self.total_tokens > (self.max_tokens - self.buffer_tokens) and len(self.messages) > 1:
            removed = self.messages.pop(0)
            self.total_tokens -= removed.token_count

    def get_context(self) -> list[Message]:
        """Get current context messages."""
        return self.messages.copy()

    def summarize_and_compact(self, summarizer: callable) -> None:
        """Summarize older messages to save space."""
        if len(self.messages) < 5:
            return

        # Keep first (system) and last 3 messages
        to_summarize = self.messages[1:-3]
        if not to_summarize:
            return

        # Generate summary
        summary_content = summarizer([m.content for m in to_summarize])
        summary_tokens = len(summary_content) // 4  # Rough estimate

        summary_message = Message(
            role="system",
            content=f"[Previous conversation summary: {summary_content}]",
            token_count=summary_tokens,
        )

        # Replace old messages with summary
        old_tokens = sum(m.token_count for m in to_summarize)
        self.messages = [self.messages[0], summary_message] + self.messages[-3:]
        self.total_tokens = self.total_tokens - old_tokens + summary_tokens

    def tokens_remaining(self) -> int:
        """Get tokens remaining before limit."""
        return self.max_tokens - self.total_tokens - self.buffer_tokens


# Usage example:
# ctx = ContextManager(max_tokens=128000)
# ctx.add_message(Message(role="user", content="Hello", token_count=5))
'''

    def _generate_tool_validator_code(self) -> str:
        """Generate tool validator code."""
        return '''"""Tool input/output schema validator."""

from typing import Any
from pydantic import BaseModel, ValidationError
import json


class ToolValidator:
    """Validates tool inputs and outputs against schemas."""

    def __init__(self):
        self.schemas: dict[str, tuple[type[BaseModel] | None, type[BaseModel] | None]] = {}

    def register_tool(
        self,
        tool_name: str,
        input_schema: type[BaseModel] | None = None,
        output_schema: type[BaseModel] | None = None,
    ) -> None:
        """Register schemas for a tool."""
        self.schemas[tool_name] = (input_schema, output_schema)

    def validate_input(self, tool_name: str, input_data: dict[str, Any]) -> tuple[bool, str | None]:
        """
        Validate tool input against schema.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if tool_name not in self.schemas:
            return True, None  # No schema registered

        input_schema, _ = self.schemas[tool_name]
        if input_schema is None:
            return True, None

        try:
            input_schema(**input_data)
            return True, None
        except ValidationError as e:
            return False, str(e)

    def validate_output(self, tool_name: str, output_data: Any) -> tuple[bool, str | None]:
        """
        Validate tool output against schema.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if tool_name not in self.schemas:
            return True, None

        _, output_schema = self.schemas[tool_name]
        if output_schema is None:
            return True, None

        try:
            if isinstance(output_data, dict):
                output_schema(**output_data)
            return True, None
        except ValidationError as e:
            return False, str(e)

    def wrap_tool(self, tool_name: str, tool_fn: callable) -> callable:
        """Wrap a tool function with validation."""
        def wrapped(**kwargs) -> Any:
            # Validate input
            is_valid, error = self.validate_input(tool_name, kwargs)
            if not is_valid:
                raise ValueError(f"Invalid input for {tool_name}: {error}")

            # Call tool
            result = tool_fn(**kwargs)

            # Validate output
            is_valid, error = self.validate_output(tool_name, result)
            if not is_valid:
                raise ValueError(f"Invalid output from {tool_name}: {error}")

            return result
        return wrapped


# Usage example:
# from pydantic import BaseModel
#
# class SearchInput(BaseModel):
#     query: str
#     max_results: int = 10
#
# class SearchOutput(BaseModel):
#     results: list[dict]
#     total: int
#
# validator = ToolValidator()
# validator.register_tool("search", SearchInput, SearchOutput)
#
# @validator.wrap_tool("search")
# def search(query: str, max_results: int = 10):
#     # Your search logic
#     pass
'''

    def save_all(self, output_dir: Path, source_path: Path | None = None) -> list[Path]:
        """Save all artifacts to the output directory.

        Every file is written atomically. When ``source_path`` is given, the
        whole set is refused before anything is written if any artifact path
        would overwrite the input trace.
        """
        output_dir = Path(output_dir)

        artifacts = self.generate_all()
        manifest = {
            "artifacts": [
                {
                    "name": a.name,
                    "type": a.artifact_type,
                    "description": a.description,
                }
                for a in artifacts
            ]
        }
        planned = [(output_dir / a.name, a.content) for a in artifacts]
        planned.append((output_dir / "manifest.json", json.dumps(manifest, indent=2)))

        for path, _ in planned:
            if aliases_source(path, source_path):
                raise ValueError(f"Artifact {path.name} must not overwrite the input trace")

        output_dir.mkdir(parents=True, exist_ok=True)
        for path, content in planned:
            atomic_write_text(path, content)

        return [path for path, _ in planned]
