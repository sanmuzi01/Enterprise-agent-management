"""
工具自动扫描器
启动时自动导入 service/tools/ 下所有 .py 文件，
触发 @ToolRegistry.register 装饰器执行，实现"零配置注册"。
1. 自动发现：新增工具只要在 tools/ 目录建文件，不用改任何代码
2. 显式注册：每个工具仍需写 @ToolRegistry.register（不写=不注册）
3. 安全排除：__init__.py 和 base.py 不扫描（避免循环导入）
4. 异常隔离：单个工具文件导入失败不影响其他工具
"""
import importlib  # 动态导入模块
import pkgutil #扫描包里的模块
from utils.logger_handler import get_logger
logger = get_logger("tools_autoload")
def _autoload_tools():
    #扫描当前包目录，自动导入所有工具模块.pkgutil.iter_modules 遍历该目录下所有模块
    count = 0
    failed = []
    for finder,module_name,is_pkg in pkgutil.iter_modules(__path__):
        # 排除基础文件（避免循环导入和重复注册）
        if module_name in("base","__init__", "langchain_adapter", "executor"):
            continue
        try:
            # importlib.import_module 用完整包路径导入模块
            importlib.import_module(f".{module_name}", package=__name__)
            count +=1
            logger.info(f"自动加载工具模块: {module_name}")
        except Exception as e:
            # 单个工具加载失败不影响其他工具
            failed.append(module_name)
            logger.error(f"加载工具模块失败: {module_name}, 错误: {e}")
    logger.info(f"工具自动扫描完成: 成功 {count} 个, 失败 {len(failed)} 个 {failed}")
    return count

# 启动时自动执行扫描
_autoload_tools()
