"""The 「回复方式」 appendix the bot adds to every state prompt (ARCHITECTURE §3.4)."""

from __future__ import annotations

from alicedev.agent_tools.model import ToolName

from alicedev.dsl.model import (
    AgentState,
    HumanState,
    ReplySpec,
    Scenario,
    TerminalState,
    is_visible,
)

_HEADER = "---\n## 回复方式（alicedev）"


def reply_instructions(
    scenario: Scenario, state: AgentState, *, agent_ref: str, tools: tuple[ToolName, ...] = ()
) -> str:
    """Append the state's declared independent tool names and runtime discovery entry point."""
    lines: list[str] = [_HEADER, ""]
    lines.append("向群聊回复与报告状态只能通过 `chat_reply`；调用成功之前不要结束本轮。")
    lines.append("`chat_reply` 的参数是 `{ reply?, transition? }`，两者至少带一个。")
    lines.append("")
    if state.reply is not None:
        lines.append("### 回复群聊（reply）")
        lines.append("你的回复会带上本会话的标记「%n 名称」发到群里。")
        lines.extend(_spec_lines(state.reply))
    else:
        lines.append("### 本状态不对群聊可见")
        lines.append(
            "这个状态下你说的话不会发到群里。工作完成（或无法继续）时，必须调用 `chat_reply` "
            "并用 `transition` 报告下一个状态；只有进入可见状态时才附带 `reply`。"
        )
    lines.append("")
    if state.next:
        lines.append("### 报告下一个状态（transition）")
        lines.append(
            '格式：`transition: {"state": "<状态名>", "data": {"<键>": "<字符串值>"}}`。'
            "只能报告下面列出的状态；bot 校验通过后才推进。"
        )
        for target_name in state.next:
            lines.extend(_target_lines(scenario, target_name))
    else:
        lines.append("本状态没有可报告的下一个状态，只需要用 `reply` 回复群聊。")
    if tools:
        lines.append("")
        lines.extend(_tool_lines(agent_ref, tools))
    return "\n".join(lines).rstrip() + "\n"


def _tool_lines(agent_ref: str, tools: tuple[ToolName, ...]) -> list[str]:
    return [
        "### 独立工具（alicedev）",
        f"你的 agent_ref 是 `{agent_ref}`；工具代表本会话发起人执行操作，不是聊天指令。",
        "调用前阅读 skill `alicedev`，以运行时发现结果中的参数 schema 和权限为准：",
        f"`alicedev tools --agent {agent_ref}`",
        "本状态声明的工具：" + "、".join(f"`{tool.value}`" for tool in tools),
        "需要发送会话链接时由 bot 直接投递；不要自行发送或转述链接。",
    ]


def _spec_lines(spec: ReplySpec) -> list[str]:
    lines = [f"- 可用的 reply.kind：{', '.join(spec.kinds)}"]
    if spec.image_templates:
        lines.append(f"- 图片模板 image_template.template：{', '.join(spec.image_templates)}")
    if spec.text_templates:
        lines.append(f"- 文字模板 text_template.template：{', '.join(spec.text_templates)}")
    if spec.stickers:
        lines.append(f"- 可用表情 sticker：{', '.join(spec.stickers)}")
    if "text" in spec.kinds:
        lines.append(
            f"- 纯文本超过 {spec.max_text_chars} 字会被自动转为图片卡片；长内容请直接用 "
            "image_template（模板 generic_card，字段 title 与 text）。"
        )
    if "file" in spec.kinds:
        lines.append("- file：path 必须是你刚写入报告目录（reports_dir）下的文件的绝对路径。")
    if "image" in spec.kinds:
        lines.append("- image：paths 是报告目录（reports_dir）下的 PNG 绝对路径列表，可带 caption。")
    return lines


def _target_lines(scenario: Scenario, name: str) -> list[str]:
    target = scenario.state(name)
    data = f"，data 必须带：{', '.join(target.data)}" if target.data else ""
    match target:
        case AgentState():
            kind = "可对话状态" if target.conversational else "内部状态"
            extra = "；可附带 reply" if target.conversational else "；不要附带 reply"
        case HumanState():
            kind = "等待人工处理的可见状态"
            extra = "；必须同时附带 reply"
        case TerminalState():
            kind = "结束状态（可见）"
            extra = "；必须同时附带 reply"
    lines = [f"- `{name}`：{kind}{data}{extra}"]
    if is_visible(target) and target.reply is not None and not isinstance(target, AgentState):
        lines.append(f"  - 这条 reply 的 kind 可用：{', '.join(target.reply.kinds)}")
    return lines
