"""OllamaService OpenAI 호환 응답 파싱 테스트."""

from office_claw_sidecar.services.ollama_service import OllamaService


def test_parse_plain_text_response():
    data = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "안녕하세요"},
                "finish_reason": "stop",
            }
        ]
    }
    reply = OllamaService.parse_chat_completions_response(data)
    assert reply == {"content": "안녕하세요", "tool_calls": [], "finish_reason": "stop"}


def test_parse_tool_calls_response():
    tool_calls = [
        {
            "id": "call_abc",
            "type": "function",
            "function": {
                "name": "calculate_column_stat",
                "arguments": '{"column": "매출", "stat": "sum"}',
            },
        }
    ]
    data = {
        "choices": [
            {
                "message": {"role": "assistant", "content": None, "tool_calls": tool_calls},
                "finish_reason": "tool_calls",
            }
        ]
    }
    reply = OllamaService.parse_chat_completions_response(data)
    assert reply["content"] == ""
    assert reply["tool_calls"] == tool_calls
    assert reply["finish_reason"] == "tool_calls"


def test_parse_empty_choices_returns_safe_defaults():
    assert OllamaService.parse_chat_completions_response({}) == {
        "content": "",
        "tool_calls": [],
        "finish_reason": "stop",
    }
    assert OllamaService.parse_chat_completions_response({"choices": []})["content"] == ""
