#生成大纲工具
import json
from langchain_core.messages import HumanMessage, SystemMessage
from service.llm.usage import extract_usage
from service.tools.base import BaseTool,ToolRegistry
@ToolRegistry.register
class OutlineGeneratorTool(BaseTool):
    #大纲生成工具
    # 显式声明：本工具需要上下文（要拿用户的API Key和model_name调LLM）
    requires_context = True
    risk_level = "read"  # 只生成文本给用户看，不产生任何状态变更
    default_system_prompt = (
        "你是一位学术论文结构设计专家。"
        "根据用户给定的主题，生成结构清晰、逻辑严谨的论文大纲。"
        "每个章节需包含标题和简要说明。"
    )
    def get_name(self) ->str:
        return "outline_generator"
    def get_description(self) -> str:
        return "根据主题生成结构化的论文大纲。当用户需要写论文提纲、文章框架时使用。"
    def get_parameters(self) ->dict:
        return {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "论文或文章的主题"
                },
                "sections": {
                    "type": "integer",
                    "description": "需要的章节数，默认5章",
                    "default": 5
                }
            },
            "required": ["topic"]
        }
    def execute(self,**kwargs) ->str:
        # 1.取参数
        topic = kwargs.get("topic","")
        sections = kwargs.get("sections",5)
        if not topic:
            return json.dumps({"error": "未提供主题"}, ensure_ascii=False)
        #2，校验上下文（requires_context=True 的工具必须先被注入 ctx）_ctx是隐藏参数
        if not self._ctx or not self._ctx.llm_client:
            return json.dumps({"error": "工具上下文未初始化"}, ensure_ascii=False)
        # 3.构造生成大纲的 prompt
        user_message = self._build_user_message(topic, sections)
        # ctx.llm_client 是 create_langchain_llm() 建的 ChatOpenAI 实例，
        # 接口是 invoke(messages)，不是 chat(messages)——之前这里调用 .chat() 从来没成功过，
        # 每次都直接进 except 返回「LLM调用失败」，这个工具实际上从未真正生成过大纲。
        messages = [
            SystemMessage(content=self.get_system_prompt()),
            HumanMessage(content=user_message),
        ]
        # 4. 调大模型
        try:
            response = self._ctx.llm_client.invoke(messages)
        except Exception as e:
            return json.dumps({"error": f"LLM调用失败: {str(e)}"}, ensure_ascii=False)
        outline = response.content or ""
        self._ctx.record_usage(extract_usage(response))
        #返回字符串结果（ReAct Loop 要求 observation 是字符串）
        result = {
                "topic": topic,
                "sections": sections,
                "outline": outline
            }
        return json.dumps(result, ensure_ascii=False)

    def _build_user_message(self, topic: str, sections: int) -> str:
        """构造生成大纲的提示词"""
        return (
            f"请为以下主题生成一份结构化的论文大纲：\n"
            f"主题：{topic}\n"
            f"要求：分 {sections} 个章节，每章包含标题和该章节要写的内容简要说明。\n"
            f"输出格式：\n"
            f"第1章 章节标题\n  - 内容要点说明\n"
            f"第2章 章节标题\n  - 内容要点说明\n"
            f"...以此类推"
        )
