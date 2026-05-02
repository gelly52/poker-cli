"""LangChain agent demo with mixed-safety tools."""
from langchain_core.tools import tool


@tool
def search_files(query: str) -> str:
    """Search files matching query."""
    import subprocess
    result = subprocess.run(f"find . -name '{query}'", shell=True, capture_output=True, text=True)
    return result.stdout


@tool
def read_doc(path: str) -> str:
    """Read a document at given path."""
    return open(path).read()


@tool
def safe_calculator(a: int, b: int) -> int:
    """Add two integers safely with explicit validation."""
    assert isinstance(a, int)
    assert isinstance(b, int)
    return a + b
