"""Z.ai Chat Completions adapter for the shared agent loop."""

import json
from typing import Any, cast

from northdesk.agent.providers import (
    SYSTEM_PROMPT,
    MalformedResponse,
    OpenAIProvider,
    PermanentAgentError,
    ToolCall,
    TransientAgentError,
    Turn,
    decision_schema,
)


class GLMProvider(OpenAIProvider):
    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        from openai import AsyncOpenAI

        # Profiles sometimes store a complete endpoint rather than an SDK base.
        base_url = base_url.rstrip("/").removesuffix("/chat/completions") + "/"
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=20,
        )
        self.model = model

    async def step(self, messages: list[dict], tools: list[dict]) -> Turn:
        from openai import APIConnectionError, APIStatusError

        chat_messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
                + "\nInvoke actual function tools to retrieve facts; never emit logging objects or "
                "describe planned tool calls as JSON text. Get customer details and relevant "
                "KB/invoice facts. Finish by calling submit_decision alone with the proposed "
                "decision. Do not combine submit_decision with other tool calls.",
            }
        ]
        for message in messages:
            if message.get("type") == "glm_assistant":
                # Includes GLM reasoning_content unchanged, as required for continuation.
                chat_messages.append(message["message"])
            elif message.get("type") == "function_call_output":
                chat_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message["call_id"],
                        "content": message["output"],
                    }
                )
            else:
                chat_messages.append(message)
        chat_tools = [
            {
                "type": "function",
                "function": {key: tool[key] for key in ("name", "description", "parameters")},
            }
            for tool in tools
        ]
        chat_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "submit_decision",
                    "description": "Return the final proposed decision. "
                    "This does not execute an action. Code validates it; "
                    "refunds need human approval.",
                    "parameters": decision_schema(),
                },
            }
        )
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=cast(Any, chat_messages),
                tools=cast(Any, chat_tools),
                tool_choice="auto",
                max_tokens=4096,
                extra_body={"reasoning_effort": "low"},
            )
        except APIConnectionError:
            raise TransientAgentError("glm_connection_error") from None
        except APIStatusError as exc:
            if exc.status_code in (408, 429) or exc.status_code >= 500:
                raise TransientAgentError(f"glm_http_{exc.status_code}") from None
            raise PermanentAgentError(f"glm_http_{exc.status_code}") from None
        if not response.choices:
            raise MalformedResponse("glm_empty_response")
        choice = response.choices[0]
        if choice.finish_reason not in ("stop", "tool_calls"):
            raise MalformedResponse("glm_incomplete_response")
        message_data = choice.message.model_dump(exclude_none=True)
        calls = []
        for call in choice.message.tool_calls or []:
            if call.type != "function":
                raise PermanentAgentError("glm_unknown_tool_type")
            calls.append(ToolCall(call.id, call.function.name, call.function.arguments))
        output = [{"type": "glm_assistant", "message": message_data}]
        final_calls = [call for call in calls if call.name == "submit_decision"]
        if final_calls:
            if len(calls) != 1:
                raise MalformedResponse("glm_mixed_decision_and_tools")
            try:
                return Turn(decision=json.loads(final_calls[0].arguments), output=output)
            except json.JSONDecodeError:
                raise MalformedResponse("glm_unreadable_decision") from None
        if calls:
            return Turn(calls=calls, output=output)
        try:
            return Turn(decision=json.loads(choice.message.content or ""), output=output)
        except json.JSONDecodeError:
            raise MalformedResponse("glm_unreadable_decision") from None
