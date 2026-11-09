"""Split maintenance manuals, embed them with Qwen, and store them in Milvus Lite."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import OpenAI
from pymilvus import MilvusClient

EMBEDDING_MODEL = "qwen3.7-text-embedding"
COLLECTION_NAME = "manuals"
EMBEDDING_BATCH_SIZE = 20
CHUNK_SIZE = 300
CHUNK_OVERLAP = 40


def _load_manual_chunks(manuals_dir: Path) -> list[dict[str, str | int]]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks: list[dict[str, str | int]] = []

    for path in sorted(manuals_dir.glob("*.txt")):
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            continue

        title = lines[0]
        entries = lines[1:] or lines
        chunk_index = 0
        for entry in entries:
            for text in splitter.split_text(entry):
                chunks.append(
                    {
                        "text": f"{title}\n{text}",
                        "source_file": path.name,
                        "manual_title": title,
                        "chunk_index": chunk_index,
                    }
                )
                chunk_index += 1

    if not chunks:
        raise RuntimeError(f"在 {manuals_dir} 中没有找到可向量化的手册内容。")
    return chunks


def _embed_chunks(client: OpenAI, chunks: list[dict[str, str | int]]) -> list[list[float]]:
    vectors: list[list[float]] = []
    texts = [str(chunk["text"]) for chunk in chunks]

    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + EMBEDDING_BATCH_SIZE]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        if len(response.data) != len(batch):
            raise RuntimeError(
                f"Embedding API 返回 {len(response.data)} 条向量，"
                f"但本批次有 {len(batch)} 条文本。"
            )
        vectors.extend(item.embedding for item in sorted(response.data, key=lambda item: item.index))

    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1:
        raise RuntimeError(f"Embedding API 返回了不一致的向量维度：{sorted(dimensions)}")
    return vectors


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")

    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "未配置 API Key，请在项目根目录 .env 中设置 DASHSCOPE_API_KEY。"
        )

    base_url = (
        os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    manuals_dir = project_root / "data" / "manuals"
    db_path = project_root / "data" / "milvus_lite.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    chunks = _load_manual_chunks(manuals_dir)
    embedding_client = OpenAI(api_key=api_key, base_url=base_url)
    vectors = _embed_chunks(embedding_client, chunks)
    dimension = len(vectors[0])

    milvus = MilvusClient(str(db_path))
    try:
        if milvus.has_collection(COLLECTION_NAME):
            milvus.drop_collection(COLLECTION_NAME)

        milvus.create_collection(
            collection_name=COLLECTION_NAME,
            dimension=dimension,
            metric_type="COSINE",
        )
        rows = [
            {
                "id": index,
                "vector": vector,
                **chunk,
            }
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True), start=1)
        ]
        result = milvus.insert(collection_name=COLLECTION_NAME, data=rows)

        matches = milvus.search(
            collection_name=COLLECTION_NAME,
            data=[vectors[0]],
            anns_field="vector",
            limit=1,
            output_fields=["text", "source_file", "manual_title"],
        )
        if not matches or not matches[0]:
            raise RuntimeError("向量已写入，但 Milvus Lite 未能检索到测试记录。")

        print("手册向量化与存储完成")
        print(f"模型: {EMBEDDING_MODEL}")
        print(f"向量维度: {dimension}")
        print(f"手册切片数: {len(chunks)}")
        print(f"写入数量: {result.get('insert_count', len(rows))}")
        print(f"Milvus 集合: {COLLECTION_NAME}")
        print(f"数据库文件: {db_path}")
        print(f"检索验证: {matches[0][0]['entity']['source_file']}")
    finally:
        milvus.close()


if __name__ == "__main__":
    main()
