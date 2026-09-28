"""常用单位换算工具（长度、重量、温度、面积、体积）。

纯查表 + 算术，不需要网络（汇率这类需要实时数据的换算不在这里做，容易给出过时的
错误结果，交给需要联网的场景处理）。
"""
import json

from service.tools.base import BaseTool, ToolRegistry

# 换算基准：每类单位换算到一个基准单位的系数
_LENGTH_TO_METER = {
    "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
    "inch": 0.0254, "ft": 0.3048, "yard": 0.9144, "mile": 1609.344,
    "毫米": 0.001, "厘米": 0.01, "米": 1.0, "公里": 1000.0, "千米": 1000.0,
    "英寸": 0.0254, "英尺": 0.3048, "码": 0.9144, "英里": 1609.344,
}
_WEIGHT_TO_GRAM = {
    "mg": 0.001, "g": 1.0, "kg": 1000.0, "t": 1_000_000.0,
    "oz": 28.349523125, "lb": 453.59237,
    "毫克": 0.001, "克": 1.0, "千克": 1000.0, "公斤": 1000.0, "吨": 1_000_000.0,
    "斤": 500.0, "两": 50.0, "盎司": 28.349523125, "磅": 453.59237,
}
_AREA_TO_SQMETER = {
    "sqm": 1.0, "sqkm": 1_000_000.0, "sqft": 0.09290304, "acre": 4046.8564224,
    "平方米": 1.0, "平方公里": 1_000_000.0, "亩": 666.6667, "公顷": 10_000.0, "平方英尺": 0.09290304,
}
_VOLUME_TO_LITER = {
    "ml": 0.001, "l": 1.0, "gallon": 3.785411784,
    "毫升": 0.001, "升": 1.0, "加仑": 3.785411784,
}

_CATEGORY_TABLES = {
    "length": _LENGTH_TO_METER,
    "weight": _WEIGHT_TO_GRAM,
    "area": _AREA_TO_SQMETER,
    "volume": _VOLUME_TO_LITER,
}


def _convert_temperature(value: float, from_unit: str, to_unit: str) -> float:
    aliases = {"c": "celsius", "摄氏度": "celsius", "f": "fahrenheit", "华氏度": "fahrenheit",
               "k": "kelvin", "开尔文": "kelvin", "celsius": "celsius",
               "fahrenheit": "fahrenheit", "kelvin": "kelvin"}
    fu = aliases.get(from_unit.strip().lower())
    tu = aliases.get(to_unit.strip().lower())
    if not fu or not tu:
        raise ValueError(f"不支持的温度单位: {from_unit} / {to_unit}，可用 celsius/fahrenheit/kelvin")
    # 统一先转摄氏度
    if fu == "fahrenheit":
        celsius = (value - 32) * 5 / 9
    elif fu == "kelvin":
        celsius = value - 273.15
    else:
        celsius = value
    if tu == "fahrenheit":
        return celsius * 9 / 5 + 32
    if tu == "kelvin":
        return celsius + 273.15
    return celsius


@ToolRegistry.register
class UnitConverterTool(BaseTool):
    """长度/重量/面积/体积/温度换算。"""
    risk_level = "read"  # 纯计算，不产生任何状态变更

    def get_name(self) -> str:
        return "unit_converter"

    def get_description(self) -> str:
        return (
            "单位换算工具。支持长度（mm/cm/m/km/inch/ft/yard/mile 及中文米/公里/英尺等）、"
            "重量（mg/g/kg/t/oz/lb 及中文克/千克/斤/两等）、面积（平方米/平方公里/亩/公顷/平方英尺等）、"
            "体积（ml/l/gallon 及中文毫升/升/加仑）、温度（celsius/fahrenheit/kelvin，即摄氏/华氏/开尔文）。"
            "不做汇率换算（汇率会过期，需要联网查实时数据）。"
        )

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "要换算的数值"},
                "from_unit": {"type": "string", "description": "原单位，如 km、斤、celsius"},
                "to_unit": {"type": "string", "description": "目标单位，如 m、kg、fahrenheit"},
            },
            "required": ["value", "from_unit", "to_unit"],
        }

    def execute(self, **kwargs) -> str:
        value = kwargs.get("value")
        from_unit = str(kwargs.get("from_unit") or "").strip()
        to_unit = str(kwargs.get("to_unit") or "").strip()
        if value is None or not from_unit or not to_unit:
            return json.dumps({"error": "需要提供 value、from_unit、to_unit"}, ensure_ascii=False)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return json.dumps({"error": "value 必须是数值"}, ensure_ascii=False)

        from_key, to_key = from_unit.strip().lower(), to_unit.strip().lower()
        # 温度单独处理（不是线性比例换算）
        if from_key in ("c", "f", "k", "celsius", "fahrenheit", "kelvin", "摄氏度", "华氏度", "开尔文"):
            try:
                result = _convert_temperature(value, from_unit, to_unit)
            except ValueError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)
            return json.dumps({
                "value": value, "from_unit": from_unit, "to_unit": to_unit,
                "result": round(result, 6),
            }, ensure_ascii=False)

        for table in _CATEGORY_TABLES.values():
            if from_key in table and to_key in table:
                base = value * table[from_key]
                result = base / table[to_key]
                return json.dumps({
                    "value": value, "from_unit": from_unit, "to_unit": to_unit,
                    "result": round(result, 6),
                }, ensure_ascii=False)

        return json.dumps({
            "error": f"不支持从 {from_unit} 换算到 {to_unit}（两个单位必须属于同一类：长度/重量/面积/体积/温度）",
        }, ensure_ascii=False)
