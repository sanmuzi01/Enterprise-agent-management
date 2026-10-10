"""
Reranker工厂：动态创建重排序模型客户端
和 EmbeddingFactory 一样的设计思路：
- 不硬编码 if model_name=="xx" 这种分支
- 统一走 RerankRegistry 查注册表
- 新增rerank模型时，只要 xxx_reranker.py 里 RerankRegistry.register()，这里自动支持
"""
from typing import Optional
from utils.logger_handler import get_logger
from service.rag.rerank.base import BaseReranker, RerankRegistry

logger = get_logger("rerank_factory")
class RerankFactory:
    """Reranker工厂类：根据模型名创建对应的Reranker客户端"""
    @classmethod
    def create(cls,
               model_name:Optional[str]=None)->Optional[BaseReranker]:
        """创建Reranker实例
        :param model_name: 模型名（例如 "BAAI/bge-reranker-v2-m3"）
        传 None 则不创建（上层判断为不启用rerank）
        :return: BaseReranker 子类实例；找不到抛 ValueError
        当前只有本地BGE Rerank，不需要Key
        如果以后加智谱Rerank API，可以在这里加 api_key 参数，
        然后传给子类的构造函数，和EmbeddingFactory一样扩展"""
        if not model_name:
            return None
        # 1. 从注册表查客户端类
        reranker_class = RerankRegistry.get(model_name)
        if reranker_class is None:
            available = ",".join(RerankRegistry.list_all())
            raise ValueError(
                f"未知的Rerank模型: {model_name}，"
                f"可选: {available if available else '无（请先注册模型）'}"
            )
            # 2. 实例化（本地模型不需要Key，只传model_name）
        logger.info(f"创建Reranker: {model_name} (类: {reranker_class.__name__})")
        return reranker_class(model_name=model_name)