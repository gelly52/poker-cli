"""User input flows directly to shell — classic command injection."""
import subprocess


def run_command(user_input: str) -> str:
    command = "echo " + user_input
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    return result.stdout
