#定义统一接口：
from abc import abstractmethod,ABC
from typing import List

from starlette.concurrency import run_in_threadpool

class BaseEmbedding(ABC):
    """嵌入模型抽象基类
       【为什么要抽象基类？】
       和你的 BaseLLM 一样，定义"所有Embedding厂商必须实现什么方法"。
       这样 EmbeddingFactory.create() 返回的一定是 BaseEmbedding 类型，
       上层代码调 embed_texts() 时，不用关心底层是智谱还是OpenAI，接口统一。
       """
    def __init__(self,api_key:str,api_url:str=None,model_name:str=None):
        self.api_key = api_key
        self.api_url = api_url
        self.model_name = model_name

    @abstractmethod
    def embed_texts(self,texts:List[str])->List[List[float]]:
        """"
        批量文本→向量
        :param texts: 文本列表
        :return: 向量列表，每个向量是float列表，顺序和输入一致
        """
        pass
    @abstractmethod
    def embed_query(self,query:str)->List[float]:
        """单条查询→向量
        :param query: 用户问题
        :return: 单个向量
        """
        pass

    async def aembed_texts(self, texts: List[str]) -> List[List[float]]:
        """异步批量嵌入。

        默认把同步实现放到线程池执行，避免阻塞 FastAPI 事件循环。
        远程 HTTP 型客户端可以覆盖此方法，使用真正的异步 HTTP 连接。
        """

        return await run_in_threadpool(self.embed_texts, texts)

    async def aembed_query(self, query: str) -> List[float]:
        """异步查询嵌入。"""

        return await run_in_threadpool(self.embed_query, query)

    @abstractmethod
    def get_dimension(self)->int:
        """
        返回向量维度（不同模型维度不同：智谱256/1024/2048，OpenAI 1536）
        为什么要有这个方法？
        向量库创建collection时要指定维度，不同模型维度不一样，
        必须能从客户端拿到维度，否则存进去维度不匹配会报错。
        """
        pass
# ========== 全局注册表（插件模式核心） ==========

class EmbeddingRegistry:
    """
    嵌入模型注册表：所有嵌入客户端类都要在导入时注册到这里
    因为每个 xxx_embedding.py 都会 import BaseEmbedding，
    顺手就能 import 注册表，不用额外加依赖。
    而且基类文件是最稳定的（不会频繁改动），适合放全局单例。
    """
    # 字典：{模型名: 客户端类}
    # 用私有变量 _registry，外部只能通过 register/get/list 访问
    _registry: dict = {}

    @classmethod
    def register(cls, model_names: list, client_class):
        """
        注册一个或多个模型名对应的客户端类
        :param model_names: 模型名列表，如 ["embedding-3", "embedding-2"]
        :param client_class: 对应的客户端类（必须继承 BaseEmbedding）
        因为一个客户端类可以对应多个模型名：
          ZhipuEmbedding 对应 embedding-3 和 embedding-2
          OpenAIEmbedding 对应 ada-002 / 3-small / 3-large
        批量注册，不用每个模型名写一行 register。
        """
        # 防御性检查：必须继承 BaseEmbedding
        if not issubclass(client_class, BaseEmbedding):
            raise TypeError(
                f"注册失败：{client_class.__name__} 必须继承 BaseEmbedding"
            )
        for name in model_names:
            # 重复注册覆盖（而不是报错），允许后面的注册覆盖前面的
            # 为什么？用户自定义模型想覆盖内置实现时方便
            if name in cls._registry:
                print(f"[EmbeddingRegistry] 覆盖已有模型: {name} → {client_class.__name__}")
            cls._registry[name] = client_class
            print(f"[EmbeddingRegistry] 注册模型: {name} → {client_class.__name__}")

    @classmethod
    def get(cls, model_name: str):
        """根据模型名获取客户端类，找不到返回 None"""
        return cls._registry.get(model_name)

    @classmethod
    def list_all(cls):
        """列出所有已注册的模型名"""
        return list(cls._registry.keys())
