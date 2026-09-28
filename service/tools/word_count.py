import re
import json                              # 新增
from service.tools.base import BaseTool, ToolRegistry
@ToolRegistry.register
class WordCountTool(BaseTool):
    risk_level = "read"  # 纯计算，不产生任何状态变更

    def get_name(self) -> str:
        return "word_count"
    def get_description(self) -> str:
        return ("统计文本字数、段落数、句子数。"
                "当用户询问字数、文章长度时调用。")

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "需要统计的文本"
                }
            },
            "required": ["text"]
        }

    def execute(self, **kwargs)->str:
        text = kwargs.get("text")
        if not text:
            return json.dumps({"error": "未提供文本"}, ensure_ascii=False)
        # 中文字数
        char_count = len(re.findall(r'[\u4e00-\u9fff]', text))
        # 段落
        paragraph_count = len([p for p in text.split("\n")
                if p.strip()])
        # 句子
        sentence_count = len(re.findall(r'[。！？.!?]+', text))
        result = {
            "char_count": char_count,
            "paragraph_count": paragraph_count,
            "sentence_count": sentence_count
        }
        return json.dumps(result, ensure_ascii=False)