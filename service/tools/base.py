"""
Tool 工具系统基础
和 Embedding/Rerank 一样的插件模式：
  - BaseTool：抽象基类，所有工具必须实现
  - ToolRegistry：全局注册表，工具自动注册
  Factory通过Registry动态创建Embedding：文本 → 向量
  Tool：接收参数 → 执行操作 → 返回结果.需要描述自己的功能，让LLM知道什么时候该调它
"""
from abc import ABC,abstractmethod
import os
from typing import Dict,Any,List,Optional

from utils.logger_handler import get_logger

_registry_logger = get_logger("tool_registry", console_level=40)   # 注册明细只进文件日志，不刷终端


class ToolPermissionError(Exception):
    """工具权限不足时抛出"""
    pass


class ToolContext:
    """工具执行上下文：由 Agent Runtime 创建并注入,工具只管"用 client 调 LLM"，不关心 Key/DB 这些环境细节
    架构层级：
        Agent Runtime (取Key、建Client、读YML prompt)
            ↓ 注入
        ToolContext (携带 client + 可选prompt)
            ↓ 使用
        Tool (组装user_message → 调client.chat → 返回结果)
        用法（在 ToolExecutor 里）：
        ctx = ToolContext(llm_client=client, user_id=uid, system_prompt=agent_prompt)
        tool.set_context(ctx)
        result = tool.execute(topic="...")"""
    def __init__(self, llm_client=None, user_id: int = None,
                 agent_id: int = None, system_prompt: str = None,
                 permissions: Dict[str, Any] = None,
                 resource_roots: List[str] = None,
                 skill_bundles: Dict[str, Any] = None):
        self.llm_client = llm_client          # 已配置好Key的 BaseLLM 实例
        self.user_id = user_id                # 用户ID（日志/隔离）
        self.agent_id = agent_id              # AgentID（多Agent场景）
        self.system_prompt = system_prompt    # 用户自定义prompt（覆盖工具默认）
        self.permissions = permissions or {"network": False, "file_read": [], "exec": False}
        self.resource_roots = [os.path.abspath(path) for path in (resource_roots or []) if path]
        # {skill 名: {"root": 脚本包目录, "scripts": [相对路径]}}，run_skill_script 工具用
        self.skill_bundles: Dict[str, Any] = skill_bundles or {}
        self.usage_log: List[Dict[str, int]] = []  # 工具内部调 LLM 的用量，engine 每轮跑完会取走汇总

    def record_usage(self, usage: Optional[Dict[str, int]]) -> None:
        """需要调 LLM 的工具（requires_context=True）调用 LLM 后，把用量记在这里。"""
        if usage:
            self.usage_log.append(usage)

    def drain_usage(self) -> List[Dict[str, int]]:
        """取走并清空累积的用量记录（ReActEngine 每次工具节点跑完调用一次）。"""
        log, self.usage_log = self.usage_log, []
        return log

    def can_use_network(self) -> bool:
        return bool(self.permissions.get("network", False))

    def can_exec(self) -> bool:
        return bool(self.permissions.get("exec", False))

    def read_resource(self, relative_path: str, max_chars: int = 20000) -> str:
        allowed = {str(item).replace("\\", "/") for item in self.permissions.get("file_read", [])}
        normalized = os.path.normpath(relative_path or "").replace("\\", "/")
        if normalized not in allowed:
            raise ToolPermissionError(f"未授权读取资源: {relative_path}")
        if normalized.startswith("../") or os.path.isabs(normalized):
            raise ToolPermissionError(f"非法资源路径: {relative_path}")
        for root in self.resource_roots:
            candidate = os.path.abspath(os.path.join(root, normalized))
            if candidate.startswith(root + os.sep) and os.path.isfile(candidate):
                with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read(max_chars)
        raise ToolPermissionError(f"资源不存在或不可读: {relative_path}")


class BaseTool(ABC):
    """工具抽象基类
        1. 我叫什么名字（name）
        2. 我能干什么（description）
        3. 我需要什么参数（parameters schema）
        """
    def __init__(self):
        self.name = self.get_name()
        self.description = self.get_description()
        self.parameters = self.get_parameters()
        self._ctx = None  # 新增：执行上下文（需要DB的工具用）

    # 类属性：是否需要上下文,默认 False（无状态工具），子类可覆盖为 True（需要DB/用户隔离的工具）
    requires_context: bool = False
    required_permissions: Dict[str, Any] = {
        "network": False,
        "file_read": False,
        "exec": False,
    }
    # 第五轮审计 P0-2：工具风险等级，决定 ReAct 循环里能不能被自动执行。
    #   read       —— 纯查询，无状态变更，随时可以自动执行
    #   write      —— 会变更状态，但只是草稿/可反复修改，不是最终动作，可以自动执行
    #   high_risk  —— submit/approve/reject/delete 这类真正产生业务后果、难以撤销
    #                  的操作，不能由模型在同一轮里自动执行，必须走
    #                  service/tool_confirmation_service.py 的用户确认流程
    # 默认取最严的 high_risk（安全默认值）：新工具如果忘了显式标注等级，宁可多一次
    # 不必要的确认，也不能让一个真正高危的操作被漏掉、悄悄允许自动执行。
    risk_level: str = "high_risk"

    def set_context(self, ctx: ToolContext):
        """注入执行上下文（由 ToolExecutor 在执行前调用）"""
        self._ctx = ctx
    # 类属性：工具的默认 system prompt（子类可覆盖）
    # 优先级：ctx.system_prompt(用户自定义) > default_system_prompt(工具默认)
    default_system_prompt: str = ""
    def get_system_prompt(self) -> str:
        """获取实际使用的 system_prompt
        - 如果 ctx 带了用户自定义 prompt → 用用户的
        - 否则 → 用工具自己的 default_system_prompt
        """
        if self._ctx and self._ctx.system_prompt:
            return self._ctx.system_prompt
        return self.default_system_prompt

    def check_permissions(self):
        requirements = self.required_permissions or {}
        if not self._ctx:
            if any(bool(value) for value in requirements.values()):
                raise ToolPermissionError(f"工具 {self.name} 需要权限上下文")
            return
        if requirements.get("network") and not self._ctx.can_use_network():
            raise ToolPermissionError(f"工具 {self.name} 需要 network 权限")
        if requirements.get("exec") and not self._ctx.can_exec():
            raise ToolPermissionError(f"工具 {self.name} 需要 exec 权限")
        if requirements.get("file_read") and not self._ctx.permissions.get("file_read"):
            raise ToolPermissionError(f"工具 {self.name} 需要 file_read 权限")
    @abstractmethod
    def get_name(self)->str:
        #工具名称（LLM用它来调用工具）
        pass
    @abstractmethod
    def get_description(self)->str:
        #工具描述（告诉LLM什么时候该用这个工具)
        pass
    @abstractmethod
    def get_parameters(self)->str:
        """参数schema（JSON Schema格式，告诉LLM需要传什么参数）
        示例：
        {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "要计算的数学表达式，如 1+2*3"
                }
            },
            "required": ["expression"]
        }
        """
        pass
    @abstractmethod
    def execute(self,**kwargs)->str:
        """执行工具，返回结果文本
    param kwargs: LLM传来的参数
    return: 工具执行结果（字符串，会作为observation返回给LLM）
    note: 需要调LLM的子类，将 requires_context 设为 True，
    通过 self._ctx.llm_client 调用大模型，通过 get_system_prompt() 获取提示词
        """
        pass
    def to_openai_format(self)->Dict[str,Any]:
        #转换成OpenAI function calling格式
        return {
            "type":"function",
            "function":{
                "name":self.name,
                "description":self.description,
                "parameters":self.parameters,
            }
        }

# ========== 全局注册表（和EmbeddingRegistry一模一样的设计） ==========
class ToolRegistry:
    """工具注册表"""
    _registry:Dict[str,type]={}

    @classmethod
    def register(cls,tool_class):
        #注册工具类（自动用类名作为key）
        if not issubclass(tool_class,BaseTool):
            raise TypeError(f"注册失败：{tool_class.__name__} 必须继承 BaseTool")
        # 先实例化一下拿到name
        instance =  tool_class()
        name = instance.name
        if name in cls._registry:
            _registry_logger.warning("[ToolRegistry] 覆盖已有工具: %s → %s", name, tool_class.__name__)
        cls._registry[name] = tool_class
        _registry_logger.debug("[ToolRegistry] 注册工具: %s → %s", name, tool_class.__name__)
        return tool_class

    @classmethod
    def get(cls, name: str):
        """根据工具名获取工具类"""
        return cls._registry.get(name)

    @classmethod
    def get_all_tools(cls) -> List[BaseTool]:
        """获取所有已注册工具的实例列表"""
        return [tool_class() for tool_class in cls._registry.values()]

    @classmethod
    def get_all_schemas(cls) -> List[Dict[str, Any]]:
        """获取所有工具的OpenAI格式schema（传给LLM用）"""
        return [tool.to_openai_format() for tool in cls.get_all_tools()]

    @classmethod
    def list_all(cls) -> List[str]:
        """列出所有已注册的工具名"""
        return list(cls._registry.keys())
