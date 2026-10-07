"""系统提示词。业务经验写在 skills/ 目录下的 SOP 文档里，按任务加载，改 SOP 不用改代码。"""

from pathlib import Path

BASE_ROLE = """你是一家亚马逊跨境电商公司的资深运营，向老板（公司唯一的人）汇报。
公司在赛狐 ERP 里管理 5 家亚马逊店铺。你拿到的数字都已经由代码从数据库算好，不要自己重新计算或编造数字；
需要更多明细时调用工具查询。

工作原则：
- 结论先行，用中文、用老板能直接决策的语言写。
- 每条建议都必须能落到具体对象（店铺、ASIN、广告活动、关键词/搜索词）和具体数值上。
- 区分事实和推断；数据不足以下结论时，放进 questions_for_owner 让老板补充，而不是猜。
- 你现在只能提建议，不能执行任何操作。所有建议都会进入待审批列表。
- 优先级：先止损（严重问题、明显浪费），再抓机会，最后是优化项。
"""


def load_skills(skills_dir: Path, names: list[str]) -> str:
    parts = []
    for name in names:
        path = skills_dir / f"{name}.md"
        if path.exists():
            parts.append(f'<skill name="{name}">\n{path.read_text(encoding="utf-8").strip()}\n</skill>')
    return "\n\n".join(parts)


def system_prompt(skills_dir: Path, skill_names: list[str]) -> str:
    skills = load_skills(skills_dir, skill_names)
    if not skills:
        return BASE_ROLE
    return BASE_ROLE + "\n下面是公司的运营 SOP，分析和建议必须遵循：\n\n" + skills
