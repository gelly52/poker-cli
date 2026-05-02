"""RAG demo：含外部抓取 loader + 默认 chunk 的 vectorstore + retriever→prompt 拼接。

启动：/audit rag 应识别 WebBaseLoader / Chroma.from_documents / .as_retriever / .similarity_search
并给出注入面评估。
"""
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import WebBaseLoader
from langchain_core.messages import HumanMessage, SystemMessage


def build_index():
    loader = WebBaseLoader("http://untrusted.example.com/docs")
    docs = loader.load()
    vs = Chroma.from_documents(docs, embedding=None)
    return vs.as_retriever()


def answer(question, retriever, llm):
    docs = retriever.similarity_search(question)
    context = "\n".join(d.page_content for d in docs)
    msgs = [
        SystemMessage(content=f"You are an assistant. Use this context: {context}"),
        HumanMessage(content=question),
    ]
    return llm.invoke(msgs)
