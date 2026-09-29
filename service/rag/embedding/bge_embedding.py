"""
BGE 本地 Embedding 实现
对应模型：BAAI/bge-small-zh, BAAI/bge-base-zh, BAAI/bge-large-zh
1. 不调HTTP API，本地跑模型（用 sentence-transformers 库）
2. 不需要API Key
3. 首次加载会下载模型（几百MB~几GB），之后从本地加载
4. 维度由模型决定，不能改（bge-small-zh=512, bge-base-zh=768, bge-large-zh=1024）
【什么时候用？】
- 离线环境（没网络）
- 数据敏感不能外传（向量化也不能走云API）
- 省钱（OpenAI/智谱按token收费，本地免费）
"""
from utils.logger_handler import get_logger
from typing import List
from service.rag.embedding.base import BaseEmbedding

logger = get_logger("bge_embedding")
class BGEEmbedding(BaseEmbedding):
    """BGE本地Embedding客户端"""
    # 各模型默认维度
    _MODEL_DIMS = {
        "BAAI/bge-small-zh-v1.5": 512,
        "BAAI/bge-base-zh-v1.5": 768,
        "BAAI/bge-large-zh-v1.5": 1024,
    }

    def __init__(self,api_key:str=None,api_url:str= None,model_name:str="BAAI/bge-small-zh-v1.5"):
        super().__init__(api_key,api_url,model_name)
        self.dimension = self._MODEL_DIMS.get(model_name,512)
        # 模型实例（懒加载，第一次用才加载）
        self._model = None
    def _get_model(self):
        #懒加载本地模型（第一次调用时加载，之后复用）
        if self._model is None:
            # 按需导入，不用bge就不装sentence-transformers
            from sentence_transformers import SentenceTransformer
            logger.info(f"加载BGE模型: {self.model_name}（首次加载较慢）")
            self._model = SentenceTransformer(self.model_name)
            logger.info(f"BGE模型加载完成，维度={self.dimension}")
        return self._model
    def get_dimension(self) ->int:
        return self.dimension
    def embed_texts(self,texts:List[str]) ->List[List[float]]:
        if not texts:
            return []
        model = self._get_model()
        # encode 直接返回 numpy 数组，转成 list
        vectors = model.encode(texts, normalize_embeddings=True)
        # normalize_embeddings=True: 向量归一化（长度=1），
        # 这样余弦相似度等于点积，向量库检索更快
        logger.info(f"BGE嵌入完成，共 {len(vectors)} 条向量")
        return vectors.tolist()

    def embed_query(self,query:str)->List[float]:
        model = self._get_model()
        vectors = model.encode([query], normalize_embeddings=True)
        return vectors[0].tolist()
# ========== 自动注册 ==========
from service.rag.embedding.base import EmbeddingRegistry
EmbeddingRegistry.register(
    model_names=[
        "BAAI/bge-small-zh-v1.5",
        "BAAI/bge-base-zh-v1.5",
        "BAAI/bge-large-zh-v1.5",
    ],
    client_class=BGEEmbedding,
)