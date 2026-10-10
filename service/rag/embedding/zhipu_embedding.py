"""
智谱 Embedding 实现
对应模型：embedding-3, embedding-2
【为什么单独一个文件？】
和 glm_client.py 一样，厂商特定的调用逻辑隔离在这里。
换厂商时只新增一个 xxx_embedding.py，不改其他文件。
"""
import os
import requests
from typing import List
from dotenv import load_dotenv
import httpx

from service.rag.embedding.base import BaseEmbedding
from service.http_resilience import async_request_with_retry, request_with_retry
from utils.logger_handler import get_logger
load_dotenv()
logger = get_logger("zhipu_embedding")
class ZhipuEmbedding(BaseEmbedding):
    """智谱Embedding客户端
       强制实现 embed_texts/embed_query/get_dimension 三个方法，
       工厂返回的类型统一是 BaseEmbedding，上层不用关心具体厂商。
       """
    # 智谱API端点（和OpenAI兼容）
    ZHIPU_EMBEDDING_URL = "https://open.bigmodel.cn/api/paas/v4/embeddings"
    # 智谱单次请求最多64条文本
    BATCH_SIZE = 64

    def __init__(self,api_key:str,api_url:str=None,model_name:str="embedding-3"):
        # 调父类的 __init__，初始化 api_key / api_url / model_name
        super().__init__(api_key,api_url,model_name)
        # 从.env读维度
        self.dimension = int(os.getenv("EMBEDDING_DIM","256"))

    def get_dimension(self) ->int:
        """返回向量维度（向量库创建collection时要用）"""
        return self.dimension
    def embed_texts(self,texts:List[str]) ->List[List[float]]:
        """批量文本→向量（分批调用智谱API）
        :param texts: 文本列表
        :return: 向量列表，顺序和输入一致
        """
        if not texts:
            return []
        all_vectors=[]
        # 分批处理，每批最多 BATCH_SIZE=64 条
        for i in range(0,len(texts),self.BATCH_SIZE):
            batch = texts[i:i+self.BATCH_SIZE]
            vectors = self._call_api(batch)
            all_vectors.extend(vectors)
            logger.info(f"智谱嵌入批次 {i // self.BATCH_SIZE + 1} 完成，本批 {len(batch)} 条")
        logger.info(f"智谱嵌入完成，共 {len(all_vectors)} 条向量")
        return all_vectors
    def embed_query(self,query:str) ->List[float]:
        """单条查询→向量"""
        vectors = self.embed_texts([query])
        return vectors[0] if vectors else []

    async def aembed_texts(self, texts: List[str]) -> List[List[float]]:
        """异步批量文本嵌入，减少资料检索接口阻塞。"""

        if not texts:
            return []
        all_vectors = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i:i + self.BATCH_SIZE]
            vectors = await self._acall_api(batch)
            all_vectors.extend(vectors)
            logger.info(f"智谱异步嵌入批次 {i // self.BATCH_SIZE + 1} 完成，本批 {len(batch)} 条")
        logger.info(f"智谱异步嵌入完成，共 {len(all_vectors)} 条向量")
        return all_vectors

    async def aembed_query(self, query: str) -> List[float]:
        """异步单条查询嵌入。"""

        vectors = await self.aembed_texts([query])
        return vectors[0] if vectors else []

    def _call_api(self,texts:List[str])->List[List[float]]:
        """真正调智谱API（内部方法，下划线开头）
        把"发HTTP请求"和"业务编排(分批/日志)"分开，
        以后如果要加重试/缓存/限流，只改这个方法，上层不动。
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload={
            "model":self.model_name,
            "input":texts,
            "dimensions":self.dimension
        }
        try:
            url = self.api_url or self.ZHIPU_EMBEDDING_URL
            resp = request_with_retry(
                service_name=f"embedding:{self.model_name}",
                sender=lambda timeout: requests.post(url, headers=headers, json=payload, timeout=timeout),
                timeout_env="EMBEDDING_REQUEST_TIMEOUT_SECONDS",
                default_timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        # 按 index 排序，确保输出顺序和输入一致
        # 不排序会导致chunk和它的向量错位
            embeddings = sorted(data["data"],key = lambda x:x["index"])
            return [item["embedding"]for item in embeddings]
        except requests.exceptions.RequestException as e:
            logger.error(f"智谱Embedding API调用失败: {e}")
            raise
        except (KeyError, IndexError) as e:
            logger.error(f"智谱Embedding响应格式异常: {e}")
            raise

    async def _acall_api(self, texts: List[str]) -> List[List[float]]:
        """异步调用智谱 Embedding API。"""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model_name,
            "input": texts,
            "dimensions": self.dimension
        }
        try:
            url = self.api_url or self.ZHIPU_EMBEDDING_URL
            resp = await async_request_with_retry(
                service_name=f"embedding:{self.model_name}",
                sender=lambda client: client.post(url, headers=headers, json=payload),
                timeout_env="EMBEDDING_REQUEST_TIMEOUT_SECONDS",
                default_timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in embeddings]
        except httpx.HTTPError as e:
            logger.error(f"智谱Embedding API异步调用失败: {e}")
            raise
        except (KeyError, IndexError) as e:
            logger.error(f"智谱Embedding异步响应格式异常: {e}")
            raise
# ========== 自动注册（插件模式） ==========
# 所以只要 zhipu_embedding.py 被 import 过，embedding-3/embedding-2 就自动注册好了
from service.rag.embedding.base import EmbeddingRegistry

EmbeddingRegistry.register(
    model_names=["embedding-3", "embedding-2"],
    client_class=ZhipuEmbedding,
)
