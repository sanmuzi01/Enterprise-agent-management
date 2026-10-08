"""前端页面里不能出现代码里的名字（函数名、工具名、枚举值、模型标识、部门代码、事件名……）。

三道守门：
1. 后端每注册一个内置工具、每个内置模型，前端 frontend/src/utils/displayNames.ts 里都必须有对应的中文名
   （否则界面上只能显示“自定义工具”，或者把模型标识原样露出来）；
2. 页面模板里不允许把这些内部字段直接打印到页面上，必须经过 xxxLabel() / xxxDisplayName() 转成中文；
3. 写死在页面里的 placeholder / title / aria-label 文案里不能带 snake_case 这类代码名。
真实浏览器里的动态检查见开发时用过的脚本思路：登录后逐页读取页面文字，匹配 snake_case / camelCase / 函数调用。"""
import importlib
import os
import pathlib
import pkgutil
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"
NAMES_FILE = SRC / "utils" / "displayNames.ts"


def _object_keys(source: str, const_name: str) -> set:
    match = re.search(r"const " + const_name + r"[^=]*= \{\n(.*?)\n\}\n", source, re.S)
    assert match, f"displayNames.ts 里找不到 {const_name}"
    keys = set()
    for line in match.group(1).splitlines():
        found = re.match(r"\s*(?:'([^']+)'|([A-Za-z_][\w]*))\s*:", line)
        if found:
            keys.add(found.group(1) or found.group(2))
    return keys


class DisplayNameCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = NAMES_FILE.read_text(encoding="utf-8")

    def test_every_builtin_tool_has_a_chinese_name(self):
        os.environ.setdefault("CONSOLE_LOG_LEVEL", "CRITICAL")
        import service.tools as tools_package
        from service.tools.base import ToolRegistry
        for module in pkgutil.iter_modules(tools_package.__path__):
            importlib.import_module(f"service.tools.{module.name}")
        builtin = {tool.name for tool in ToolRegistry.get_all_tools() if type(tool).__module__.startswith("service.tools")}
        self.assertGreater(len(builtin), 40, "没有读到内置工具，测试本身有问题")
        missing = sorted(builtin - _object_keys(self.source, "TOOL_NAMES"))
        self.assertEqual(missing, [], f"这些内置工具在 displayNames.ts 的 TOOL_NAMES 里没有中文名：{missing}")

    def test_no_stale_tool_names_in_the_table(self):
        import service.tools as tools_package
        from service.tools.base import ToolRegistry
        for module in pkgutil.iter_modules(tools_package.__path__):
            importlib.import_module(f"service.tools.{module.name}")
        builtin = {tool.name for tool in ToolRegistry.get_all_tools() if type(tool).__module__.startswith("service.tools")}
        stale = sorted(_object_keys(self.source, "TOOL_NAMES") - builtin)
        self.assertEqual(stale, [], f"这些名字在 TOOL_NAMES 里，但后端已经没有这个工具了：{stale}")

    def test_every_builtin_model_has_a_chinese_name(self):
        from service.llm import model_catalog, offline_demo
        known = set(model_catalog.CHAT_MODELS) | set(model_catalog.EMBEDDING_MODELS) | {offline_demo.MODEL_NAME}
        missing = sorted(known - _object_keys(self.source, "MODEL_NAMES"))
        self.assertEqual(missing, [], f"这些内置模型在 displayNames.ts 的 MODEL_NAMES 里没有中文名：{missing}")

    def test_every_diagnose_check_has_a_chinese_name(self):
        """系统诊断页的检查项标题：后端能产生的每个检查项，displayNames.ts 的 DIAGNOSE_CHECKS 里都要有中文名。"""
        from unittest.mock import patch
        from service.config_validation import validate_runtime_config
        base = {"APP_ENV": "production", "DB_USER": "root", "AUDIT_DB_USER": "root", "ENTERPRISE_DB_USER": "root",
                "DB_PASSWORD": "x", "ENTERPRISE_DB_PASSWORD": "x", "MYSQL_ROOT_PASSWORD": "x", "REDIS_URL": "redis://127.0.0.1:6379/0",
                "AUTH_TOKEN_ENDPOINT_ENABLED": "1", "SESSION_COOKIE_SECURE": "0", "SMS_EXPOSE_DEV_CODE": "1"}
        produced = set()
        for provider in ("console", "webhook", "aliyun"):
            with patch.dict(os.environ, {**base, "SMS_PROVIDER": provider}, clear=True):
                produced |= {check["name"] for check in validate_runtime_config()["checks"]}
        # FasdtApi/main.py 的 system_diagnose 自己追加的检查项
        produced |= {"redis", "sms", "database", "static", "knowledge_files", "skills", "vector_db"}
        self.assertGreater(len(produced), 25, "没有读到后端的检查项，测试本身有问题")
        missing = sorted(produced - _object_keys(self.source, "DIAGNOSE_CHECKS"))
        self.assertEqual(missing, [], f"这些诊断检查项在 displayNames.ts 的 DIAGNOSE_CHECKS 里没有中文名：{missing}")

    def test_tool_chinese_names_contain_no_code_identifiers(self):
        for name, label in re.findall(r"^\s*(\w+): '([^']+)',?$", self.source[self.source.index("TOOL_NAMES"):], re.M):
            self.assertIsNone(re.search(r"[a-z]+_[a-z]+|[a-z]+[A-Z][a-z]+", label), f"{name} 的中文名里还带着代码名：{label}")


# 这些字段是内部标识：直接打印到页面上就是“把代码里的名字露给用户”。必须经过 xxxLabel() / xxxDisplayName()。
BANNED_FIELDS = r"tool_name|tool_names|model_name|task_type|target_type|source_type|department_code|event_type|consumer|agent_type|lifecycle_status"
MUSTACHE = re.compile(r"\{\{(.*?)\}\}", re.S)
# 直接打印一个属性（或者“属性 || 属性/字符串”这种兜底写法）：这就是把内部字段原样露给用户。
# 条件表达式、函数调用（xxxLabel() / xxxDisplayName()）、.map(...) 之类都算已经转换过了。
BARE_PRINT = re.compile(r"^[\w.?]+(\s*\|\|\s*(?:[\w.?]+|'[^']*'|\"[^\"]*\"))*$")


def _template_of(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.find("<template>")
    end = text.rfind("</template>")
    return text[start:end] if start >= 0 and end > start else ""


class TemplateLeakTests(unittest.TestCase):
    def test_templates_do_not_print_internal_fields_directly(self):
        problems = []
        for path in sorted(SRC.rglob("*.vue")):
            for number, line in enumerate(_template_of(path).splitlines(), 1):
                for block in MUSTACHE.findall(line):
                    if re.search(rf"\b({BANNED_FIELDS})\b", block) and BARE_PRINT.match(block.strip()):
                        problems.append(f"{path.relative_to(SRC)}（模板第 {number} 行）：{{{{{block.strip()}}}}}")
        self.assertEqual(problems, [], "这些位置把内部字段直接打印到了页面上，请用 utils/displayNames.ts 里的函数转成中文：\n" + "\n".join(problems))

    def test_static_attribute_texts_have_no_snake_case(self):
        problems = []
        pattern = re.compile(r'(?<![:@\w-])(placeholder|title|aria-label|label)="([^"]*\b[a-z]+_[a-z0-9_]+\b[^"]*)"')
        for path in sorted(SRC.rglob("*.vue")):
            for number, line in enumerate(_template_of(path).splitlines(), 1):
                for match in pattern.finditer(line):
                    problems.append(f"{path.relative_to(SRC)}（模板第 {number} 行）：{match.group(1)}=\"{match.group(2)}\"")
        self.assertEqual(problems, [], "页面里写死的提示文字带着代码名：\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
