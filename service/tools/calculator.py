"""安全数学表达式计算工具。

LLM 做精确算术不可靠（尤其是多位数乘除、幂运算），这个工具给它一个可以信赖的计算器。
安全性：不用 eval()/exec()，而是把表达式解析成 AST 后只允许遍历白名单节点类型
（数字、四则运算、乘方、取模、括号、一元正负号）和白名单函数（math 模块的常用函数 +
几个内置函数），任何其他节点（属性访问、函数定义、导入、下标……）直接拒绝，
从根上堵住"表达式里塞代码"的注入路径。
"""
import ast
import json
import math
import operator

from service.tools.base import BaseTool, ToolRegistry

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "sqrt": math.sqrt, "log": math.log, "log2": math.log2, "log10": math.log10,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "floor": math.floor, "ceil": math.ceil, "factorial": math.factorial,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}
_MAX_POWER_EXPONENT = 1000  # 防止 9**9**9 这种指数炸内存/CPU


class SafeEvalError(ValueError):
    pass


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise SafeEvalError(f"不支持的常量: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise SafeEvalError(f"不支持的运算符: {type(node.op).__name__}")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if op is operator.pow and (abs(right) > _MAX_POWER_EXPONENT):
            raise SafeEvalError("指数过大，拒绝计算")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise SafeEvalError(f"不支持的一元运算符: {type(node.op).__name__}")
        return op(_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise SafeEvalError("不支持的函数调用")
        if node.keywords:
            raise SafeEvalError("不支持关键字参数")
        args = [_eval_node(a) for a in node.args]
        return _FUNCTIONS[node.func.id](*args)
    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise SafeEvalError(f"未知标识符: {node.id}")
    raise SafeEvalError(f"不支持的表达式结构: {type(node).__name__}")


def safe_eval(expression: str) -> float:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise SafeEvalError(f"表达式语法错误: {exc}") from exc
    return _eval_node(tree)


@ToolRegistry.register
class CalculatorTool(BaseTool):
    """安全数学计算器：四则运算、乘方、取模，以及 sqrt/log/sin/cos 等常用函数。"""
    risk_level = "read"  # 纯计算，不产生任何状态变更

    def get_name(self) -> str:
        return "calculator"

    def get_description(self) -> str:
        return (
            "计算一个数学表达式，返回精确结果。当用户要求做算术、单位换算前的数值计算、"
            "百分比、复利等需要精确数字的场景时使用，不要自己心算容易出错的位数。"
            "支持 + - * / // % **、括号，以及 sqrt/log/sin/cos/tan/abs/round/floor/ceil/factorial 等函数，"
            "常数 pi、e。"
        )

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "要计算的数学表达式，如 '(1+2)*3/4' 或 'sqrt(2)**2'",
                }
            },
            "required": ["expression"],
        }

    def execute(self, **kwargs) -> str:
        expression = str(kwargs.get("expression") or "").strip()
        if not expression:
            return json.dumps({"error": "未提供表达式"}, ensure_ascii=False)
        if len(expression) > 200:
            return json.dumps({"error": "表达式过长"}, ensure_ascii=False)
        try:
            result = safe_eval(expression)
        except SafeEvalError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except ZeroDivisionError:
            return json.dumps({"error": "除数不能为 0"}, ensure_ascii=False)
        except (OverflowError, ValueError) as exc:
            return json.dumps({"error": f"计算失败: {exc}"}, ensure_ascii=False)
        return json.dumps({"expression": expression, "result": result}, ensure_ascii=False)
