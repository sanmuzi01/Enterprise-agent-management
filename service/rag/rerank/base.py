from abc import abstractmethod,ABC
from typing import List
class BaseReranker(ABC):
    """重排序模型抽象基类
    和 BaseEmbedding 的设计一样：
    - 定义统一接口，所有Rerank模型必须实现
    - Factory通过RerankRegistry动态创建
    - 上层代码不关心底层是BGE还是智谱
    - Embedding：文本 → 向量（用于检索）
    - Reranker：  (query, document) → 分数（用于重排序）
    """
    def __init__(self,model_name:str=None):
        self.model_name = model_name

    @abstractmethod
    def rerank(self,query:str,documents:List[str],top_n:int=3):
        """对documents按与query的相关性重新排序
            :param query: 用户问题
            :param documents: 待排序的文档列表（向量检索返回的）
            :param top_n: 返回前N条
            :return: [(原始索引, 分数), ...] 按分数从高到低排序
            原始索引是为了让上层能找到对应的chunk
              """
        pass
# ========== 全局注册表（插件模式核心） ==========
class RerankRegistry:
    """重排序模型注册表"""
    _registry:dict={}
    @classmethod
    def register(cls,model_names:list,reranker_class):
        """注册模型名 → reranker类"""
        if not issubclass(reranker_class,BaseReranker):
            raise TypeError(
                f"注册失败：{reranker_class.__name__} 必须继承 BaseReranker"
            )
        for name in model_names:
            if name in cls._registry:
                print(f"[RerankRegistry] 覆盖已有模型: {name} → {reranker_class.__name__}")
            cls._registry[name] = reranker_class
            print(f"[RerankRegistry] 注册模型: {name} → {reranker_class.__name__}")

    @classmethod
    def get(cls, model_name: str):
        """根据模型名获取reranker类"""
        return cls._registry.get(model_name)

    @classmethod
    def list_all(cls):
        """列出所有已注册的模型名"""
        return list(cls._registry.keys())