#图表生成工具
import json
import uuid
from pathlib import Path
from service.tools.base import BaseTool,ToolRegistry

@ToolRegistry.register
class ChartGeneratorTool(BaseTool):
    """数据图表生成工具
        纯绘图工具，不需要 LLM（requires_context=False）
        支持：柱状图(bar)、折线图(line)、饼图(pie)"""
    risk_level = "read"  # 只生成图片文件，不产生业务状态变更
    # 图表保存目录 = 项目根/static/charts/（相对前端可访问）
    CHART_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "charts"
    SUPPORTED_TYPES = ("bar", "line", "pie")

    def get_name(self) -> str:
        return "chart_generator"

    def get_description(self) -> str:
        return ("根据结构化数据生成图表（柱状图bar、折线图line、饼图pie）。"
                "当用户需要数据可视化、画图表时使用。返回图表图片访问URL。")

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": ["bar", "line", "pie"],
                    "description": "图表类型：bar=柱状图、line=折线图、pie=饼图"
                },
                "title": {
                    "type": "string",
                    "description": "图表标题"
                },
                "data": {
                    "type": "object",
                    "description": (
                        "图表数据。结构："
                        "{categories:[类目列表], series:[{name:系列名, values:[数值列表]}]}。"
                        "饼图只需1个系列。"
                    ),
                    "properties": {
                        "categories": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "X轴类目（饼图即标签）"
                        },
                        "series": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "values": {
                                        "type": "array",
                                        "items": {"type": "number"}
                                    }
                                },
                                "required": ["name", "values"]
                            }
                        }
                    },
                    "required": ["categories", "series"]
                },
                "xlabel": {
                    "type": "string",
                    "description": "X轴标签（饼图不需要）"
                },
                "ylabel": {
                    "type": "string",
                    "description": "Y轴标签（饼图不需要）"
                }
            },
            "required": ["chart_type", "data"]
        }

    def execute(self, **kwargs) -> str:
        # 1. 延迟导入 matplotlib（避免未安装时影响整个工具系统加载）
        try:
            import matplotlib
            matplotlib.use('Agg')  # 非交互式后端
            import matplotlib.pyplot as plt
        except ImportError:
            return json.dumps(
                {"error": "服务器未安装 matplotlib，无法生成图表"},
                ensure_ascii=False
            )

        # 2. 中文显示支持
        plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False

        # 3. 参数提取
        chart_type = kwargs.get("chart_type", "")
        title = kwargs.get("title", "")
        data = kwargs.get("data", {})
        xlabel = kwargs.get("xlabel", "")
        ylabel = kwargs.get("ylabel", "")

        # 4. 校验图表类型
        if chart_type not in self.SUPPORTED_TYPES:
            return json.dumps(
                {"error": f"不支持的图表类型: {chart_type}，"
                          f"可选: {list(self.SUPPORTED_TYPES)}"},
                ensure_ascii=False
            )

        # 5. 规范化数据结构（兼容 LLM 可能传来的多种格式）
        categories, series, err = self._normalize_data(data)
        if err:
            return json.dumps(err, ensure_ascii=False)

        # 饼图只取第一个系列
        if chart_type == "pie":
            series = [series[0]]

        # 6. 绘图（异常隔离）
        fig, ax = plt.subplots(figsize=(8, 6))
        filename = None
        try:
            if chart_type == "bar":
                import numpy as np
                x = np.arange(len(categories))
                width = 0.8 / len(series)
                for i, s in enumerate(series):
                    offset = (i - len(series) / 2 + 0.5) * width
                    ax.bar(x + offset, s["values"], width, label=s["name"])
                ax.set_xticks(x)
                ax.set_xticklabels(categories)
                if xlabel:
                    ax.set_xlabel(xlabel)
                if ylabel:
                    ax.set_ylabel(ylabel)
                if len(series) > 1:
                    ax.legend()

            elif chart_type == "line":
                for s in series:
                    ax.plot(categories, s["values"], marker='o', label=s["name"])
                if xlabel:
                    ax.set_xlabel(xlabel)
                if ylabel:
                    ax.set_ylabel(ylabel)
                if len(series) > 1:
                    ax.legend()

            elif chart_type == "pie":
                ax.pie(series[0]["values"], labels=categories,
                       autopct='%1.1f%%', startangle=90)
                ax.axis('equal')

            if title:
                ax.set_title(title)

            # 7. 保存图片
            self.CHART_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"chart_{uuid.uuid4().hex[:8]}.png"
            fig.savefig(self.CHART_DIR / filename, dpi=100, bbox_inches='tight')
        except Exception as e:
            return json.dumps(
                {"error": f"绘图失败: {str(e)}"},
                ensure_ascii=False
            )
        finally:
            plt.close(fig)

        # 8. 只返回可访问的相对URL，不泄露服务器绝对路径
        result = {
            "chart_type": chart_type,
            "title": title,
            "category_count": len(categories),
            "series_count": len(series),
            "image_url": f"/static/charts/{filename}"
        }
        return json.dumps(result, ensure_ascii=False)

    def _normalize_data(self, data) -> tuple:
        """规范化数据结构，兼容多种 LLM 传参格式
        支持的输入格式：
          1. {categories:[...], series:[{name, values:[...]}]}  ← 标准格式
          2. {categories:[...], series:[80,90,85]}              ← series 是数值列表
          3. {categories:[...], series:[{name, data:[...]}]}    ← 用 data 代替 values
          4. {"语文":80, "数学":90}                              ← 简单字典 {类目:数值}
          5. {labels:[...], values:[...]}                        ← labels/values 命名
        返回: (categories, series, error_dict)
          error_dict 为 None 表示成功
        """
        # 非字典直接拒绝
        if not isinstance(data, dict):
            return None, None, {"error": f"data 必须是对象，实际是 {type(data).__name__}"}

        # 格式4：纯字典 {类目: 数值}
        if not any(k in data for k in ("categories", "series", "labels", "values")):
            try:
                categories = list(data.keys())
                values = [float(v) for v in data.values()]
                return (
                    categories,
                    [{"name": "数据", "values": values}],
                    None,
                )
            except (TypeError, ValueError):
                return None, None, {"error": "data 字典值必须为数值"}

        # 提取 categories（兼容 categories / labels）
        categories = data.get("categories") or data.get("labels")

        # 提取 series 原始值（兼容 series / values）
        series_raw = data.get("series")
        if series_raw is None:
            # 格式5：labels + values
            values = data.get("values")
            if values is not None and categories:
                try:
                    return (
                        list(categories),
                        [{"name": "数据", "values": [float(v) for v in values]}],
                        None,
                    )
                except (TypeError, ValueError):
                    return None, None, {"error": "values 必须是数值列表"}

        if not categories or not series_raw:
            return None, None, {
                "error": "数据格式错误，需包含 categories(类目数组) 和 series(数据数组)"
            }

        categories = list(categories)
        series = []
        # 格式2：series 是数值列表
        if series_raw and isinstance(series_raw[0], (int, float)):
            try:
                return (
                    categories,
                    [{"name": "数据", "values": [float(v) for v in series_raw]}],
                    None,
                )
            except (TypeError, ValueError):
                return None, None, {"error": "series 数值列表包含非数字"}

        # 格式1/3：series 是对象列表
        for i, s in enumerate(series_raw):
            if not isinstance(s, dict):
                return None, None, {"error": f"series[{i}] 必须是对象"}
            name = s.get("name") or f"系列{i+1}"
            # 兼容 values 和 data 两种字段名
            vals = s.get("values")
            if vals is None:
                vals = s.get("data")
            if vals is None:
                return None, None, {
                    "error": f"series[{i}] 缺少 values 字段（或 data 字段）"
                }
            try:
                vals = [float(v) for v in vals]
            except (TypeError, ValueError):
                return None, None, {"error": f"series[{i}].values 必须是数值列表"}
            series.append({"name": name, "values": vals})

        return categories, series, None