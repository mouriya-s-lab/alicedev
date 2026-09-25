"""The 「回复方式」 appendix the bot adds to every state prompt (ARCHITECTURE §3.4)."""

from __future__ import annotations

from typing import Sequence

from alicedev.dsl.model import (
    AgentState,
    Command,
    HumanState,
    ReplySpec,
    Scenario,
    TerminalState,
    is_visible,
)

_HEADER = "---\n## 回复方式（alicedev）"


def reply_instructions(
    scenario: Scenario, state: AgentState, *, agent_ref: str, commands: Sequence[tuple[str, Command]]
) -> str:
    """``commands``: the state's ``agent_commands`` as (path, command), in declared order."""
    lines: list[str] = [_HEADER, ""]
    lines.append("你只能通过 `chat_reply` 工具与群聊和 bot 通信；调用成功之前不要结束本轮。")
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
    if commands:
        lines.append("")
        lines.extend(_command_lines(agent_ref, commands))
    return "\n".join(lines).rstrip() + "\n"


def _command_lines(agent_ref: str, commands: Sequence[tuple[str, Command]]) -> list[str]:
    lines = [
        "### 可用指令（alicedev run）",
        f"你可以代表本会话的发起人调用下面的 bot 指令。你的 agent 标识是 `{agent_ref}`，调用方法见 skill `alicedev`：",
        f"`alicedev run --agent {agent_ref} '<指令行>'`（先用 `alicedev commands --agent {agent_ref}` 看参数说明）。",
    ]
    for path, command in commands:
        lines.append(f"- `{command.usage}`（{path}）：{command.summary}")
    lines.append("调用结果会写明做了什么；开会话、收藏这类操作 bot 会在群里留痕，你不需要再转述一遍。")
    return lines


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
