"""Tests for poker.capabilities.audit.rag."""
import pytest

from poker.capabilities.audit.rag import audit_rag, find_rag_components


SAMPLE = '''
from langchain_community.vectorstores import Chroma, FAISS
from langchain_community.document_loaders import WebBaseLoader


def build():
    loader = WebBaseLoader("http://example.com/docs")
    docs = loader.load()
    vs = Chroma.from_documents(docs)
    retriever = vs.as_retriever()
    results = retriever.similarity_search("query")
    return FAISS(embedding_function=None, index=None, docstore=None, index_to_docstore_id={})


def regular_function(x):
    """Not RAG-related — should be ignored."""
    return x.upper()
'''


@pytest.fixture
def rag_project(tmp_path):
    f = tmp_path / "app.py"
    f.write_text(SAMPLE.lstrip("\n"), encoding="utf-8")
    return tmp_path


def test_find_rag_components_picks_vectorstore_methods(rag_project):
    comps = find_rag_components(rag_project)
    methods = {c.method for c in comps}
    assert any("Chroma.from_documents" in m for m in methods)
    assert "FAISS" in methods


def test_find_rag_components_picks_retriever_calls(rag_project):
    comps = find_rag_components(rag_project)
    kinds = {c.kind for c in comps}
    assert "retriever" in kinds
    methods = {c.method for c in comps}
    assert any("as_retriever" in m for m in methods)
    assert any("similarity_search" in m for m in methods)


def test_find_rag_components_picks_external_loader(rag_project):
    comps = find_rag_components(rag_project)
    methods = {c.method for c in comps}
    assert "WebBaseLoader" in methods


def test_audit_rag_flags_external_loader(rag_project):
    comps = find_rag_components(rag_project)
    loader = next(c for c in comps if c.method == "WebBaseLoader")
    result = audit_rag(loader, llm=None)
    checks = {r.check for r in result.risks}
    assert "external_data_source" in checks
    assert result.overall_severity in ("high", "critical")


def test_audit_rag_flags_retriever_to_prompt(rag_project):
    comps = find_rag_components(rag_project)
    retriever = next(c for c in comps if c.kind == "retriever")
    result = audit_rag(retriever, llm=None)
    checks = {r.check for r in result.risks}
    assert "retrieved_content_to_prompt" in checks


def test_find_rag_components_skips_unrelated_calls(rag_project):
    comps = find_rag_components(rag_project)
    methods = " ".join(c.method for c in comps)
    assert "regular_function" not in methods
