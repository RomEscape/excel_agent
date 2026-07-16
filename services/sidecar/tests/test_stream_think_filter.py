"""_ThinkStreamFilter: 스트리밍 중 <think>...</think> 사고 블록 억제 검증.

태그가 조각 경계에 걸쳐도 사용자에게 사고 블록이 새지 않아야 한다.
"""

from office_claw_sidecar.services.excel_tool_agent import _ThinkStreamFilter


def _run(chunks: list[str]) -> str:
    f = _ThinkStreamFilter()
    out = "".join(f.feed(c) for c in chunks)
    out += f.flush()
    return out


def test_think_block_stripped_whole():
    assert _run(["<think>reason</think>", "answer"]) == "answer"


def test_think_block_split_across_chunks():
    # <think>/</think> 태그가 여러 조각에 걸쳐 도착해도 안전하게 제거
    chunks = ["<thi", "nk>reas", "on</th", "ink>", "final ", "answer"]
    assert _run(chunks) == "final answer"


def test_no_think_block_passes_through():
    assert _run(["hello ", "world"]) == "hello world"


def test_think_prefix_then_streamed_tokens():
    chunks = ["<think>", "hmm", "</think>", "A", "B", "C"]
    assert _run(chunks) == "ABC"
