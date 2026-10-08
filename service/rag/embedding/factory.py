"""
Embedding 工厂：根据模型名创建对应的嵌入客户端
和 LLMFactory 完全对称的设计
上层代码只调 EmbeddingFactory.create(model_name, api_key)，
不用关心具体是哪个厂商的客户端。
加新厂商时：新增 xxx_embedding.py + 在 _MODEL_MAP 加一行，其他代码不动。
"""
from service.rag.embedding.base import BaseEmbedding, EmbeddingRegistry
from utils.logger_handler import get_logger

logger = get_logger("embedding_factory")
class EmbeddingFactory:
    """嵌入模型工厂"""
    # 模型名 → 客户端类 的映射
    # None 表示预留（和你的LLMFactory风格一致）
    @classmethod
    def create(
            cls,model_name:str,api_key:str=None,api_url:str=None
               )->BaseEmbedding:
        """根据模型名创建嵌入客户端
        param model_name: 模型名（如 embedding-3 / text-embedding-3-small / BAAI/bge-small-zh-v1.5）
        param api_key: API Key（本地模型如bge可传None）
        param api_url: 自定义端点（OpenAI代理/兼容服务用）
        return: BaseEmbedding 实例
                """
        client_class = EmbeddingRegistry.get(model_name)
        if client_class is None:
            cls._load_builtin_clients()          # 客户端靠导入模块来注册：还没人导入过时（比如管理后台直接测试连接）先补上
            client_class = EmbeddingRegistry.get(model_name)

        if client_class is None:
            # 没找到 → 报错
            registered = EmbeddingRegistry.list_all()
            logger.error(f"不支持的嵌入模型: {model_name}，已注册: {registered}")
            raise ValueError(
                f"不支持的嵌入模型: {model_name}\n"
                f"当前已注册: {registered}\n"
                f"新增模型：在 service/rag/embedding/ 下新建 xxx_embedding.py，"
                f"继承 BaseEmbedding，并在文件末尾调用 EmbeddingRegistry.register()"
            )

        logger.info(f"创建嵌入客户端: {model_name} (类: {client_class.__name__})")
        return client_class(api_key=api_key, api_url=api_url, model_name=model_name)

    @classmethod
    def _load_builtin_clients(cls) -> None:
        import importlib
        for module in ("zhipu_embedding", "openai_embedding", "bge_embedding"):
            try:
                importlib.import_module(f"service.rag.embedding.{module}")
            except Exception as exc:  # noqa: BLE001 —— 比如本地模型缺依赖：不影响其他客户端
                logger.debug(f"嵌入客户端 {module} 没有加载: {exc}")

    @classmethod
    def list_supported_models(cls):
        """列出所有已注册的模型（从注册表取，不是硬编码）"""
        return EmbeddingRegistry.list_all()
