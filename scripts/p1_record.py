"""Append a component's actual acceptance evidence to the project handoff."""

import re
from pathlib import Path


def record(component: str, result: str) -> None:
    path = Path("AGENTS.md")
    text = path.read_text().replace(f"| [ ] | {component} ", f"| [x] | {component} ")
    marker = "\n## 5. 下一步待办"
    text = text.replace(marker, f"\n| 2026-10-23 | Codex | {component}：{result} |\n" + marker)
    text = re.sub(
        r"^\| 代码 \|.*$",
        f"| 代码 | P1 实施中，最近独立验收通过：{component}；阶段总验收尚未完成 |",
        text,
        count=1,
        flags=re.M,
    )
    path.write_text(text)
    Path(f"docs/acceptance/{component}.md").write_text(
        f"# {component} 组件验收\n\n日期：2026-10-23。\n\n{result}\n"
    )
