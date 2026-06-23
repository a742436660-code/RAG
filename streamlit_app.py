import os

import requests
import streamlit as st

API_BASE_URL = os.getenv("RAG_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Streamlit 只是演示客户端，所有业务操作都通过 FastAPI 完成，不直接访问数据库。
st.set_page_config(page_title="Local Enterprise RAG", layout="wide")
st.title("Local Enterprise RAG")


def rerun() -> None:
    # 兼容不同 Streamlit 版本的刷新 API。
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def api_get(path: str):
    # 统一 GET 封装，后端错误会通过 raise_for_status 显示给 Streamlit。
    response = requests.get(f"{API_BASE_URL}{path}", timeout=30)
    response.raise_for_status()
    return response.json()


def api_post(path: str, json=None, files=None):
    # 上传和问答可能耗时更久，因此 POST timeout 设置得比 GET 长。
    response = requests.post(f"{API_BASE_URL}{path}", json=json, files=files, timeout=120)
    response.raise_for_status()
    return response.json()


with st.sidebar:
    # 侧边栏负责选择知识库和创建新知识库。
    st.caption(API_BASE_URL)
    if st.button("Refresh"):
        rerun()
    kb_name = st.text_input("New knowledge base")
    if st.button("Create", disabled=not kb_name.strip()):
        api_post("/knowledge-bases", json={"name": kb_name.strip()})
        rerun()

kbs = api_get("/knowledge-bases")
if not kbs:
    st.info("Create a knowledge base to begin.")
    st.stop()

kb_labels = {f"{item['name']} ({item['id'][:8]})": item for item in kbs}
selected_label = st.sidebar.selectbox("Knowledge base", list(kb_labels))
kb = kb_labels[selected_label]
kb_id = kb["id"]

tab_upload, tab_search, tab_chat, tab_docs = st.tabs(["Upload", "Search", "Chat", "Documents"])

with tab_upload:
    # 上传后后端会自动处理文档；本地 eager 模式下返回时通常已经完成索引。
    uploaded = st.file_uploader("Document", type=["pdf", "docx", "txt", "md", "markdown"])
    if uploaded and st.button("Upload and index"):
        files = {
            "file": (
                uploaded.name,
                uploaded.getvalue(),
                uploaded.type or "application/octet-stream",
            )
        }
        result = api_post(f"/knowledge-bases/{kb_id}/documents", files=files)
        st.json(result)

with tab_search:
    # Search 标签只展示检索证据，适合调试 sparse/dense/hybrid 的召回效果。
    query = st.text_input("Search query")
    mode = st.selectbox("Mode", ["hybrid_rerank", "hybrid", "sparse", "dense"])
    top_k = st.slider("Top K", min_value=1, max_value=20, value=8)
    if st.button("Search", disabled=not query.strip()):
        result = api_post(
            f"/knowledge-bases/{kb_id}/search",
            json={"query": query, "top_k": top_k, "retrieval_mode": mode},
        )
        st.caption(f"log_id={result['log_id']}")
        for item in result["results"]:
            st.markdown(f"**#{item['rank']} {item['source_filename']}** score={item['score']:.4f}")
            st.write(item["quote"])

with tab_chat:
    # Chat 标签调用 /chat，后端会检索证据、生成 citation 并保存会话。
    question = st.text_area("Question", height=100)
    if st.button("Ask", disabled=not question.strip()):
        result = api_post(
            f"/knowledge-bases/{kb_id}/chat",
            json={"query": question, "top_k": 8, "retrieval_mode": "hybrid_rerank"},
        )
        st.write(result["answer"])
        st.caption(
            f"conversation_id={result['conversation_id']} log_id={result['retrieval_log_id']}"
        )
        st.json(result["citations"])

with tab_docs:
    # Documents 标签展示知识库统计和每个文档的处理状态。
    docs = api_get(f"/knowledge-bases/{kb_id}/documents")
    stats = api_get(f"/knowledge-bases/{kb_id}/stats")
    st.json(stats)
    for doc in docs:
        with st.expander(f"{doc['original_filename']} - {doc['status']}"):
            st.json(doc)
