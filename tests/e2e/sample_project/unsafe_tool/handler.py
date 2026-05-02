"""Cross-file taint sample: handler.py 调 utils.format_cmd 把 body 拼进 shell。

启动：/trace handler.py:9:body  应跨文件追到 utils.py 的 subprocess.run。
project_root 须设为 unsafe_tool/ 才能解析到 utils。
"""
import subprocess

from utils import format_cmd


def handle(req):
    body = req.get("body", "")
    cmd = format_cmd(body)
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout
