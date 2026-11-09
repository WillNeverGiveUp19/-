"""Verify that the local Milvus Lite database can create, insert, and search."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pymilvus import MilvusClient


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "milvus_lite.db"
    collection_name = f"env_check_{uuid4().hex[:12]}"

    client = MilvusClient(str(db_path))
    collection_created = False
    try:
        client.create_collection(
            collection_name=collection_name,
            dimension=4,
            metric_type="COSINE",
        )
        collection_created = True
        client.insert(
            collection_name=collection_name,
            data=[
                {"id": 1, "vector": [0.1, 0.2, 0.3, 0.4], "text": "本地向量库测试"}
            ],
        )
        results = client.search(
            collection_name=collection_name,
            data=[[0.1, 0.2, 0.3, 0.4]],
            anns_field="vector",
            limit=1,
            output_fields=["text"],
        )

        if not results or not results[0]:
            raise RuntimeError("Milvus Lite 查询成功执行，但没有返回测试记录。")

        hit = results[0][0]
        print("Milvus Lite 连通性检查成功")
        print(f"数据库文件: {db_path}")
        print(f"命中内容: {hit.get('entity', {}).get('text')}")
        print(f"相似度: {hit.get('distance')}")
    finally:
        if collection_created:
            client.drop_collection(collection_name=collection_name)
        client.close()


if __name__ == "__main__":
    main()
