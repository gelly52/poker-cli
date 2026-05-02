"""审计 RAG 注入面：vectorstore / retriever / document loader 静态扫描。

识别 LangChain 风格 RAG 组件 → 检查数据源信任度、检索结果是否做净化、chunk size。
MVP 仅静态 AST 模式匹配，不实际跑 RAG 流程。
"""
import ast
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from poker.capabilities.audit._common import (
    Risk,
    overall_severity,
    prompt_selection,
    render_risks_block,
)
from poker.state import save_audit
from poker.workspace import iter_text_files


# ---------- 数据结构 ----------

@dataclass
class RagComponent:
    kind: str  # 'vectorstore' | 'retriever' | 'document_loader'
    method: str
    file: str
    line: int
    snippet: str = ""
    args: list[str] = field(default_factory=list)


@dataclass
class RagAuditResult:
    component: RagComponent
    risks: list[Risk] = field(default_factory=list)
    overall_severity: str = "info"
    llm_summary: str = ""


# ---------- 已知组件名 ----------

_VECTORSTORE_NAMES = {
    "Chroma", "FAISS", "Milvus", "Pinecone", "Weaviate", "Qdrant",
    "PGVector", "Redis", "ElasticsearchStore", "OpenSearchVectorSearch",
    "MongoDBAtlasVectorSearch", "LanceDB",
}
_VECTORSTORE_METHODS = {"from_documents", "from_texts", "from_embeddings"}
_RETRIEVER_METHODS = {
    "as_retriever", "similarity_search", "similarity_search_with_score",
    "max_marginal_relevance_search", "get_relevant_documents",
    "asimilarity_search",
}
_NETWORK_LOADERS = {
    "WebBaseLoader", "RecursiveUrlLoader", "AsyncHtmlLoader",
    "SitemapLoader", "GitbookLoader", "ConfluenceLoader",
    "AsyncChromiumLoader", "PlaywrightURLLoader",
}


# ---------- 静态识别 ----------

def find_rag_components(project_root: Path) -> list[RagComponent]:
    """扫 .py 找 RAG 相关调用：vectorstore / retriever / loader。"""
    components: list[RagComponent] = []
    for py_file in iter_text_files(project_root):
        if py_file.suffix != ".py":
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
        except (SyntaxError, OSError):
            continue
        rel = _rel(py_file, project_root)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                comp = _classify_call(node, content)
                if comp:
                    comp.file = rel
                    components.append(comp)
    return components


def _classify_call(call: ast.Call, content: str) -> RagComponent | None:
    """根据 call.func 形态归类为 vectorstore / retriever / document_loader。"""
    func = call.func
    snippet = _line_text(content, getattr(call, "lineno", 0))
    args = _arg_summary(call)

    if isinstance(func, ast.Name):
        if func.id in _VECTORSTORE_NAMES:
            return RagComponent(
                kind="vectorstore", method=func.id,
                file="", line=call.lineno, snippet=snippet, args=args,
            )
        if func.id in _NETWORK_LOADERS:
            return RagComponent(
                kind="document_loader", method=func.id,
                file="", line=call.lineno, snippet=snippet, args=args,
            )

    if isinstance(func, ast.Attribute):
        attr = func.attr
        base = _name_of(func.value)
        if attr in _VECTORSTORE_METHODS:
            method = f"{base}.{attr}" if base else attr
            return RagComponent(
                kind="vectorstore", method=method,
                file="", line=call.lineno, snippet=snippet, args=args,
            )
        if attr in _RETRIEVER_METHODS:
            method = f"{base}.{attr}" if base else f".{attr}"
            return RagComponent(
                kind="retriever", method=method,
                file="", line=call.lineno, snippet=snippet, args=args,
            )
    return None


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _arg_summary(call: ast.Call) -> list[str]:
    out: list[str] = []
    for a in call.args:
        try:
            out.append(ast.unparse(a)[:80])
        except Exception:
            out.append(type(a).__name__)
    for kw in call.keywords:
        try:
            out.append(f"{kw.arg}={ast.unparse(kw.value)[:80]}")
        except Exception:
            out.append(f"{kw.arg}=...")
    return out


def _line_text(content: str, line: int) -> str:
    lines = content.splitlines()
    return lines[line - 1].strip() if 0 < line <= len(lines) else ""


def _rel(file: Path, root: Path) -> str:
    try:
        return file.relative_to(root).as_posix()
    except ValueError:
        return str(file)


# ---------- 审计规则 ----------

def audit_rag(component: RagComponent, llm: Any = None) -> RagAuditResult:
    """对单个 RAG 组件做静态 + 可选 LLM 风险评估。"""
    risks: list[Risk] = []

    if component.kind == "document_loader":
        risks.append(Risk(
            check="external_data_source",
            severity="high",
            evidence=f"使用 {component.method} 从外部抓取文档，未见信任校验",
            recommendation="对外部内容做白名单 / 签名校验；untrusted 文档应隔离不直接进 prompt",
        ))

    if component.kind == "vectorstore":
        joined = " ".join(component.args).lower()
        if "http://" in joined:
            risks.append(Risk(
                check="insecure_endpoint",
                severity="high",
                evidence="vectorstore 连接使用 http:// 明文",
                recommendation="改用 https:// 或专用网络通道；避免明文泄露 embedding / query",
            ))
        if component.method.endswith(".from_documents") and not any("chunk" in a for a in component.args):
            risks.append(Risk(
                check="default_chunk_size",
                severity="low",
                evidence=f"{component.method} 未显式指定 chunk size / splitter",
                recommendation="显式 chunk_size（建议 500-1500）；过大 chunk 注入面更宽",
            ))

    if component.kind == "retriever":
        risks.append(Risk(
            check="retrieved_content_to_prompt",
            severity="medium",
            evidence=f"{component.method} 返回的内容会被拼到 prompt；数据源不可信即 prompt injection 入口",
            recommendation="对 retrieved 内容做 sanitize（剥离指令性句式 / 标注信任级别），用 system/user 分隔",
        ))

    overall = overall_severity(risks)
    llm_summary = ""
    if llm is not None and component.snippet:
        try:
            llm_summary = _llm_assess(component, llm)
        except Exception as e:
            llm_summary = f"（LLM 评估失败：{e}）"

    return RagAuditResult(
        component=component, risks=risks,
        overall_severity=overall, llm_summary=llm_summary,
    )


def _llm_assess(component: RagComponent, llm: Any) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage
    sys_prompt = (
        "你是 AI 应用安全审计员。评估给定 RAG 组件的 prompt injection 风险与数据源可信度，"
        "给出 3-5 条具体结论，每条一行。"
    )
    user_msg = (
        f"组件类型: {component.kind}\n"
        f"方法: {component.method}\n"
        f"位置: {component.file}:{component.line}\n"
        f"参数: {component.args}\n"
        f"代码片段: {component.snippet}"
    )
    response = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=user_msg)])
    content = response.content if hasattr(response, "content") else str(response)
    return content if isinstance(content, str) else str(content)


# ---------- 交互式入口 ----------

def interactive_audit_rag(project_root: Path, llm: Any, console: Console) -> None:
    components = find_rag_components(project_root)
    if not components:
        console.print("[yellow]未发现 RAG 组件（vectorstore / retriever / loader）[/yellow]")
        return

    table = Table(title=f"发现 {len(components)} 个 RAG 组件")
    table.add_column("#", style="bold")
    table.add_column("类型")
    table.add_column("方法")
    table.add_column("位置")
    for i, c in enumerate(components, 1):
        table.add_row(str(i), c.kind, c.method, f"{c.file}:{c.line}")
    console.print(table)

    selected = prompt_selection(
        components, label=lambda c: c.method, console=console, kind="组件",
    )
    if selected is None:
        return

    for c in selected:
        console.print(f"\n[bold]审计 {c.method}[/bold] [dim]({c.file}:{c.line})[/dim]")
        result = audit_rag(c, llm)
        render_risks_block(console, result.risks, result.overall_severity, result.llm_summary)
        target = f"{c.kind}_{c.method}".replace("/", "_").replace(".", "_")
        path = save_audit(project_root, "rag", target, _result_to_dict(result))
        console.print(f"[dim]结果已保存：{path}[/dim]")


def _result_to_dict(result: RagAuditResult) -> dict:
    return {
        "component": asdict(result.component),
        "risks": [asdict(r) for r in result.risks],
        "overall_severity": result.overall_severity,
        "llm_summary": result.llm_summary,
    }
