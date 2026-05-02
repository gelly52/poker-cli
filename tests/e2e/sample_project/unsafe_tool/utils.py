"""utils.format_cmd 把外部输入直接拼进 shell 命令——经典命令注入帮凶。"""


def format_cmd(user_input: str) -> str:
    return "echo " + user_input
