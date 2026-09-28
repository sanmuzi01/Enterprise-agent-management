"""日期/时间计算工具。

LLM 经常算错日期差、星期几、多少天后是几号——这些是纯确定性计算，交给代码算，
不需要也不该让模型"猜"。不需要网络、不需要权限上下文。
"""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from service.tools.base import BaseTool, ToolRegistry

_WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
_TZ_CN = ZoneInfo("Asia/Shanghai")


def _parse_date(value: str) -> date:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {value!r}，请用 YYYY-MM-DD 格式")


@ToolRegistry.register
class DatetimeCalculatorTool(BaseTool):
    """日期计算：今天日期、两个日期相差多少天、某日期加减若干天后是哪天、某日期是星期几。"""
    risk_level = "read"  # 纯计算，不产生任何状态变更

    def get_name(self) -> str:
        return "datetime_calculator"

    def get_description(self) -> str:
        return (
            "日期计算工具。支持四种操作（op 参数）："
            "now（查当前日期时间，默认北京时间）、"
            "diff（算两个日期相差多少天，需要 date1、date2）、"
            "add（某日期加/减若干天，需要 date、days，days 可为负数）、"
            "weekday（查某日期是星期几，需要 date）。"
            "当用户问'今天几号'、'还有多少天'、'N天后是几号'、'那天星期几'时使用，"
            "不要自己心算日期，容易算错闰年和月份天数。"
        )

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["now", "diff", "add", "weekday"],
                    "description": "要做的操作",
                },
                "date": {"type": "string", "description": "日期，YYYY-MM-DD（add/weekday 用）"},
                "date1": {"type": "string", "description": "起始日期，YYYY-MM-DD（diff 用）"},
                "date2": {"type": "string", "description": "结束日期，YYYY-MM-DD（diff 用）"},
                "days": {"type": "integer", "description": "要加减的天数，负数表示减（add 用）"},
            },
            "required": ["op"],
        }

    def execute(self, **kwargs) -> str:
        op = str(kwargs.get("op") or "").strip()
        try:
            if op == "now":
                now = datetime.now(_TZ_CN)
                return json.dumps({
                    "date": now.strftime("%Y-%m-%d"),
                    "time": now.strftime("%H:%M:%S"),
                    "weekday": _WEEKDAY_CN[now.weekday()],
                    "timezone": "Asia/Shanghai (UTC+8)",
                }, ensure_ascii=False)

            if op == "diff":
                d1, d2 = _parse_date(kwargs.get("date1")), _parse_date(kwargs.get("date2"))
                delta = (d2 - d1).days
                return json.dumps({
                    "date1": d1.isoformat(), "date2": d2.isoformat(),
                    "days_between": delta,
                    "description": f"{d2.isoformat()} 比 {d1.isoformat()} {'晚' if delta >= 0 else '早'} {abs(delta)} 天",
                }, ensure_ascii=False)

            if op == "add":
                d = _parse_date(kwargs.get("date"))
                days = kwargs.get("days")
                if days is None:
                    return json.dumps({"error": "add 操作需要提供 days 参数"}, ensure_ascii=False)
                result = d + timedelta(days=int(days))
                return json.dumps({
                    "date": d.isoformat(), "days": int(days), "result": result.isoformat(),
                    "weekday": _WEEKDAY_CN[result.weekday()],
                }, ensure_ascii=False)

            if op == "weekday":
                d = _parse_date(kwargs.get("date"))
                return json.dumps({"date": d.isoformat(), "weekday": _WEEKDAY_CN[d.weekday()]}, ensure_ascii=False)

            return json.dumps({"error": f"不支持的操作: {op}，可选 now/diff/add/weekday"}, ensure_ascii=False)
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
