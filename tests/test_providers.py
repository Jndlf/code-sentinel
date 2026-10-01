"""Offline-Tests der Provider-Schicht mit Fake-Clients (kein Netz, kein Key)."""
import dataclasses
import json
from types import SimpleNamespace as NS

import pytest

from agent.config import Config, ModelProfile
from agent.llm import AuditLog, run_agent
from agent.providers.anthropic_conv import AnthropicConversation
from agent.providers.base import STUB, Conversation, Step, ToolCall
from agent.providers.openai_conv import OpenAIConversation, to_openai_tools
from agent.tools import TOOL_SCHEMAS, Toolbox

SCHEMAS = [TOOL_SCHEMAS[n] for n in ("read_file", "submit_report")]


def profile(provider="anthropic", **kw):
    return ModelProfile(name="t", provider=provider, base_url="https://x.test", model="m", **kw)


def test_profile_validation(monkeypatch):
    monkeypatch.delenv("LANGDOCK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="nicht gesetzt"):
        profile().validate()
    monkeypatch.setenv("LANGDOCK_API_KEY", "k")
    profile().validate()
    with pytest.raises(ValueError, match="base_url"):
        dataclasses.replace(profile(), base_url="TODO-aus-Langdock-Doku").validate()
    with pytest.raises(ValueError, match="provider"):
        profile("gemini").validate()


def test_openai_tool_translation():
    t = to_openai_tools(SCHEMAS)[0]
    assert t["type"] == "function" and t["function"]["name"] == "read_file"
    assert t["function"]["parameters"]["required"] == ["path"]


class FakeOpenAI:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return self.replies.pop(0)


def oa_reply(text="", calls=(), pt=10, ct=5):
    tcs = [NS(id=i, function=NS(name=n, arguments=json.dumps(a))) for i, n, a in calls] or None
    return NS(model="m", usage=NS(prompt_tokens=pt, completion_tokens=ct),
              choices=[NS(finish_reason="tool_calls" if tcs else "stop",
                          message=NS(content=text, tool_calls=tcs))])


def test_openai_conversation_roundtrip_and_trim():
    fake = FakeOpenAI([oa_reply("n", [("c1", "read_file", {"path": "a"})]),
                       oa_reply("n2", [("c2", "read_file", {"path": "b"})])])
    conv = OpenAIConversation(profile("openai", max_tokens_param="max_tokens"), "SYS", SCHEMAS, 99, client=fake)
    conv.add_user("go")
    s = conv.step()
    assert s.tool_calls == [ToolCall("c1", "read_file", {"path": "a"})]
    conv.add_tool_results([("c1", "INHALT-A", False)])
    conv.step()
    conv.add_tool_results([("c2", "INHALT-B", True)])
    assert fake.calls[0]["max_tokens"] == 99 and "max_completion_tokens" not in fake.calls[0]
    conv.trim_old_results(keep=1)
    tool_msgs = [m for m in conv.messages if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == STUB and "INHALT-B" in tool_msgs[1]["content"]


class FakeAnthropic:
    def __init__(self, replies):
        self.replies = list(replies)
        self.messages = NS(create=lambda **kw: self.replies.pop(0))


def an_reply(calls=()):
    content = [NS(type="text", text="notiz")] + [
        NS(type="tool_use", id=i, name=n, input=a) for i, n, a in calls]
    return NS(model="m", stop_reason="tool_use", content=content,
              usage=NS(input_tokens=10, output_tokens=5))


def test_anthropic_conversation_trim():
    fake = FakeAnthropic([an_reply([("a", "read_file", {"path": "x"})]),
                          an_reply([("b", "read_file", {"path": "y"})])])
    conv = AnthropicConversation(profile(), "SYS", SCHEMAS, 99, client=fake)
    conv.add_user("go")
    conv.step(); conv.add_tool_results([("a", "ALT", False)])
    conv.step(); conv.add_tool_results([("b", "NEU", False)])
    conv.trim_old_results(1)
    results = [m["content"][0]["content"] for m in conv.messages
               if m["role"] == "user" and isinstance(m["content"], list)]
    assert results == [STUB, "NEU"]


class ScriptedConv(Conversation):
    base_url = "fake"

    def __init__(self, steps):
        self.steps = list(steps)
        self.results = []

    def add_user(self, text): self.users = getattr(self, "users", []) + [text]
    def step(self, force_tool=None):
        self.forced = getattr(self, "forced", []) + [force_tool]
        return self.steps.pop(0)
    def add_tool_results(self, results): self.results.append(results)
    def trim_old_results(self, keep): pass


def test_agent_loop_end_to_end_provider_neutral(tmp_path):
    (tmp_path / "A.java").write_text("class A {}")
    cfg = dataclasses.replace(Config(profile=profile()), target_repo=tmp_path)
    tools = Toolbox(cfg)
    conv = ScriptedConv([
        Step("", [ToolCall("1", "read_file", {"path": "A.java"})], 100, 10),
        Step("", [ToolCall("2", "read_file", {"path": ".env"})], 120, 10),       # gesperrt → Fehler
        Step("", [ToolCall("3", "submit_report", {"markdown": "# R"})], 140, 10),
    ])
    stats = run_agent(cfg, "go", tools, AuditLog(tmp_path.parent / "a.jsonl"), conv)
    assert stats.end_reason == "report_submitted" and tools.report == "# R"
    assert stats.input_tokens == 360 and stats.turns == 3
    assert conv.results[1][0][2] is True  # zweiter Tool-Aufruf als Fehler gemeldet


def test_token_budget_stops(tmp_path):
    cfg = dataclasses.replace(Config(profile=profile()), target_repo=tmp_path, max_input_tokens_total=150)
    conv = ScriptedConv([Step("", [ToolCall("1", "list_files", {})], 100, 1)] * 5)
    stats = run_agent(cfg, "go", Toolbox(cfg), AuditLog(tmp_path.parent / "b.jsonl"), conv)
    # Budget nach Runde 2 überschritten -> Runde 3 ist der erzwungene Abschluss
    assert stats.end_reason == "token_budget_reached" and stats.turns == 3
    assert conv.forced == [None, None, "submit_report"]


def test_openai_reasoning_effort_passed_only_when_set():
    fake = FakeOpenAI([oa_reply("x"), oa_reply("y")])
    a = OpenAIConversation(profile("openai", reasoning_effort="none"), "S", SCHEMAS, 9, client=fake)
    a.add_user("go"); a.step()
    b = OpenAIConversation(profile("openai"), "S", SCHEMAS, 9, client=fake)
    b.add_user("go"); b.step()
    assert fake.calls[0]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in fake.calls[1]


# ---- Responses-API ---------------------------------------------------------------------
from agent.providers.openai_responses_conv import OpenAIResponsesConversation, to_responses_tools


class FakeResponses:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.responses = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return self.replies.pop(0)


def rs_reply(calls=(), text="", it=10, ot=5):
    out = [NS(type="reasoning", id="rs_1", summary=[])]
    if text:
        out.append(NS(type="message", role="assistant", content=[NS(type="output_text", text=text)]))
    for cid, n, a in calls:
        out.append(NS(type="function_call", id="fc_" + cid, call_id=cid, name=n, arguments=json.dumps(a)))
    return NS(model="m", status="completed", output=out, usage=NS(input_tokens=it, output_tokens=ot))


def test_responses_tool_format_is_flat():
    t = to_responses_tools(SCHEMAS)[0]
    assert t["type"] == "function" and t["name"] == "read_file" and "function" not in t


def test_responses_roundtrip_keeps_reasoning_items_and_trims_results():
    fake = FakeResponses([rs_reply([("c1", "read_file", {"path": "a"})], "notiz"),
                          rs_reply([("c2", "read_file", {"path": "b"})])])
    prof = profile("openai-responses", reasoning_effort="medium")
    conv = OpenAIResponsesConversation(prof, "SYS", SCHEMAS, 77, client=fake)
    conv.add_user("go")
    s = conv.step()
    assert s.tool_calls == [ToolCall("c1", "read_file", {"path": "a"})] and s.text == "notiz"
    assert s.input_tokens == 10 and s.output_tokens == 5
    conv.add_tool_results([("c1", "INHALT-A", False)])
    conv.step()
    conv.add_tool_results([("c2", "INHALT-B", True)])
    first = fake.calls[0]
    assert first["instructions"] == "SYS" and first["max_output_tokens"] == 77
    assert first["reasoning"] == {"effort": "medium"} and "store" not in first
    types = [i.get("type", "message") for i in conv.items]
    assert "reasoning" in types  # Reasoning-Item wird zurückgegeben
    conv.trim_old_results(keep=1)
    outs = [i["output"] for i in conv.items if i.get("type") == "function_call_output"]
    assert outs[0] == STUB and "INHALT-B" in outs[1]


def test_responses_stateless_flags():
    fake = FakeResponses([rs_reply()])
    prof = dataclasses.replace(profile("openai-responses"), responses_stateless=True)
    conv = OpenAIResponsesConversation(prof, "S", SCHEMAS, 9, client=fake)
    conv.add_user("go"); conv.step()
    assert fake.calls[0]["store"] is False
    assert fake.calls[0]["include"] == ["reasoning.encrypted_content"]
    assert "reasoning" not in fake.calls[0]


def test_provider_names_validated(monkeypatch):
    monkeypatch.setenv("LANGDOCK_API_KEY", "k")
    profile("openai-responses").validate()


def test_forced_report_at_turn_limit_without_warning(tmp_path):
    cfg = dataclasses.replace(Config(profile=profile()), target_repo=tmp_path, max_turns=4)
    tools = Toolbox(cfg)
    endless = [Step("", [ToolCall(str(i), "list_files", {})], 10, 1) for i in range(3)]
    final = Step("", [ToolCall("f", "submit_report", {"markdown": "# Notfall-Report"})], 10, 1)
    conv = ScriptedConv(endless + [final])
    stats = run_agent(cfg, "go", tools, AuditLog(tmp_path.parent / "c.jsonl"), conv)
    assert stats.turns == 4 and stats.end_reason == "max_turns_reached"
    assert tools.report == "# Notfall-Report"           # trotz Limit ein Report
    assert conv.forced == [None, None, None, "submit_report"]
    assert not any("HINWEIS" in u for u in conv.users)   # keine Vorab-Warnung mehr
    assert any("LETZTE RUNDE" in u for u in conv.users)


def test_forced_tool_choice_formats():
    a = FakeAnthropic([an_reply()])
    AnthropicConversation(profile(), "S", SCHEMAS, 9, client=a)
    fake = FakeOpenAI([oa_reply("x")])
    c = OpenAIConversation(profile("openai"), "S", SCHEMAS, 9, client=fake)
    c.add_user("go"); c.step(force_tool="submit_report")
    assert fake.calls[0]["tool_choice"] == {"type": "function", "function": {"name": "submit_report"}}
    fr = FakeResponses([rs_reply()])
    r = OpenAIResponsesConversation(profile("openai-responses"), "S", SCHEMAS, 9, client=fr)
    r.add_user("go"); r.step(force_tool="submit_report")
    assert fr.calls[0]["tool_choice"] == {"type": "function", "name": "submit_report"}
