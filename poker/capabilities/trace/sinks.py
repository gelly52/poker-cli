"""危险 sink 定义：函数名匹配 → 风险等级 + 修复建议。

每个 SinkPattern 通过 matchers 列表匹配调用名（全限定名或方法名）。
"""
from dataclasses import dataclass


@dataclass
class SinkPattern:
    name: str
    matchers: list[str]
    description: str
    recommendation: str
    severity: str  # critical | high | medium


DANGEROUS_SINKS: list[SinkPattern] = [
    SinkPattern(
        name="subprocess (shell exec)",
        matchers=["subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output"],
        description="subprocess 调用：若 shell=True 或参数被拼接，构成命令注入",
        recommendation="禁用 shell=True；用 list 形式参数；外部输入做 shlex.quote 或白名单校验",
        severity="high",
    ),
    SinkPattern(
        name="eval / exec / compile",
        matchers=["eval", "exec", "compile"],
        description="动态执行代码：外部输入直接执行 = RCE",
        recommendation="避免 eval/exec；用 ast.literal_eval 或受限解释器",
        severity="critical",
    ),
    SinkPattern(
        name="os.system / os.popen",
        matchers=["os.system", "os.popen"],
        description="os.system / popen：参数当 shell 解释执行",
        recommendation="改用 subprocess.run([list], shell=False)",
        severity="high",
    ),
    SinkPattern(
        name="cursor.execute (SQL)",
        matchers=[".execute", ".executemany"],
        description="DB 执行：若 SQL 用 f-string / + 拼接，构成 SQL 注入",
        recommendation="改用参数化查询：cursor.execute(sql, (param,))",
        severity="high",
    ),
    SinkPattern(
        name="LLM invoke (prompt 拼接)",
        matchers=[".invoke", ".predict", ".generate", ".complete", ".chat"],
        description="把外部输入直接拼到 prompt 调 LLM = prompt injection 入口",
        recommendation="对外部内容做 sanitize 或显式标注 trusted/untrusted；用 system/user 分离",
        severity="medium",
    ),
    SinkPattern(
        name="open() write mode",
        matchers=["open"],
        description="文件写入：若路径来自外部输入，构成路径穿越",
        recommendation="对路径做白名单校验；检查 ../ 等跳出 root",
        severity="medium",
    ),
]


def find_matching_sink(call_name: str) -> SinkPattern | None:
    """根据调用名查找匹配的 sink；matcher 以 . 开头时按方法名后缀匹配。"""
    for sink in DANGEROUS_SINKS:
        for matcher in sink.matchers:
            if call_name == matcher:
                return sink
            if matcher.startswith(".") and call_name.endswith(matcher):
                return sink
    return None
