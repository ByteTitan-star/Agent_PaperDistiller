import datetime as dt
import os
import re


# 清理 UTF-16 代理字符，避免 JSON/Markdown 序列化异常。
SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

# ---------------------------------------------------------------------
# Token 消耗记录功能
# ---------------------------------------------------------------------

TOKEN_MD_PATH = r"D:\Z-Desktop\找工作\8大模型开发\实战项目\Token记录\token.md"


def log_token_usage(project_name: str, model_name: str, prompt_tokens: int, completion_tokens: int) -> None:
    """
    更新公共的 token.md 文件，追加新记录并自动汇总统计信息。
    """
    total_tokens = prompt_tokens + completion_tokens
    if total_tokens == 0:
        return

    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 确保文件夹存在
    os.makedirs(os.path.dirname(TOKEN_MD_PATH), exist_ok=True)

    lines: list[str] = []
    if os.path.exists(TOKEN_MD_PATH):
        with open(TOKEN_MD_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

    # 1. 提取历史日志数据
    logs: list[dict[str, object]] = []
    in_log_section = False
    for line in lines:
        if line.startswith("| 时间"):
            in_log_section = True
            continue
        if line.startswith("|---"):
            continue
        if in_log_section and line.startswith("|"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 6:
                try:
                    logs.append(
                        {
                            "time": parts[0],
                            "project": parts[1],
                            "model": parts[2],
                            "prompt": int(parts[3]),
                            "completion": int(parts[4]),
                            "total": int(parts[5]),
                        }
                    )
                except ValueError:
                    # 忽略解析错误的行
                    pass

    # 2. 加上本次的新记录
    logs.append(
        {
            "time": now_str,
            "project": project_name,
            "model": model_name,
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": total_tokens,
        }
    )

    # 3. 重新计算各种维度的统计信息
    global_total = sum(int(log["total"]) for log in logs)

    project_stats: dict[str, int] = {}
    model_stats: dict[str, int] = {}
    for log in logs:
        p = str(log["project"])
        m = str(log["model"])
        project_stats[p] = project_stats.get(p, 0) + int(log["total"])
        model_stats[m] = model_stats.get(m, 0) + int(log["total"])

    # 4. 重写 Markdown 文件，生成清晰的面板和日志
    try:
        with open(TOKEN_MD_PATH, "w", encoding="utf-8") as f:
            f.write("# 🤖 全局大模型 Token 消耗记录\n\n")

            f.write("## 📊 汇总数据\n")
            f.write(f"- 全局总消耗: `{global_total}` Tokens\n\n")

            f.write("### 📁 按项目统计\n")
            for p, t in project_stats.items():
                f.write(f"- {p}: `{t}` Tokens\n")
            f.write("\n")

            f.write("### 🧠 按模型统计\n")
            for m, t in model_stats.items():
                f.write(f"- {m}: `{t}` Tokens\n")
            f.write("\n")

            f.write("## 📝 详细日志\n")
            f.write("| 时间 | 项目名称 | 模型名称 | 输入 Tokens | 输出 Tokens | 总 Tokens |\n")
            f.write("|---|---|---|---|---|---|\n")
            # 倒序输出，让最新的记录在最上面
            for log in reversed(logs):
                f.write(
                    f"| {log['time']} | {log['project']} | {log['model']} | "
                    f"{log['prompt']} | {log['completion']} | {log['total']} |\n"
                )
    except Exception as e:  # pragma: no cover - 日志失败不影响主流程
        print(f"写入 token.md 失败: {e}")


def utc_now_iso() -> str:
    """返回当前 UTC 时间的 ISO 字符串。"""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def remove_surrogates(text: str) -> str:
    """
    移除字符串中的代理字符，保证后续编码安全。

    孤立的代理字符是非法 Unicode，会导致编码错误。
    """
    return SURROGATE_RE.sub("", text)


__all__ = ["SURROGATE_RE", "log_token_usage", "utc_now_iso", "remove_surrogates"]

