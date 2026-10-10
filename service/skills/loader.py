"""
Skill配置加载器
职责：读取 skills/templates/ 下的 YML 文件 → 校验 → 转成结构化dict
1.路径规范：
   Skill.config_file 存的是相对路径，如 "templates/论文写作助手.yml"
   完整路径 = service/skills/ + config_file
2.非开发者友好：
   YML写错时给清晰的报错（哪一行、什么字段错了）
3.版本控制：
   YML文件和Agent的prompt YML一样，支持Git管理
4.运行时校验：
   加载时检查 tools 里的每个工具名是否在 ToolRegistry 中存在
   不存在的工具给warning跳过（防止Skill引用了未实现的工具直接崩）
"""
import os
import yaml
from typing import Dict,List,Any

from utils.logger_handler import get_logger
from utils.cache import skill_cache
logger = get_logger("skill_loader")
# Skill YML文件所在根目录 = service/skills/
SKILLS_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "skills")
class SkillValidationError(Exception):
    #Skill配置校验失败时抛出
    pass
def _get_yml_path(config_file:str)->str:
    #（相对路径）转成绝对路径
    # 允许 config_file 带或不带 templates/ 前缀
    normalized = os.path.normpath(config_file).replace("\\", os.sep)
    if os.path.isabs(normalized) or normalized.startswith(".."):
        raise SkillValidationError(f"非法Skill配置路径: {config_file}")
    full_path = os.path.abspath(os.path.join(SKILLS_ROOT, normalized))
    root = os.path.abspath(SKILLS_ROOT)
    if not full_path.startswith(root + os.sep):
        raise SkillValidationError(f"非法Skill配置路径: {config_file}")
    return full_path

def _safe_join(root: str, relative_path: str) -> str:
    normalized = os.path.normpath(relative_path or "").replace("\\", os.sep)
    if os.path.isabs(normalized) or normalized.startswith(".."):
        raise SkillValidationError(f"非法资源路径: {relative_path}")
    full_path = os.path.abspath(os.path.join(root, normalized))
    abs_root = os.path.abspath(root)
    if full_path != abs_root and not full_path.startswith(abs_root + os.sep):
        raise SkillValidationError(f"非法资源路径: {relative_path}")
    return full_path

def _packages_base() -> str:
    # 导入的 Skill 包（资源、脚本包）都放在 <项目根>/skills_packages/ 下
    return os.path.join(os.path.dirname(SKILLS_ROOT), "skills_packages")


_ROOT_MARKERS = ("skills_packages", "skills")   # 先认更具体的 skills_packages


def _project_root() -> str:
    # 每次现算：测试会临时替换 SKILLS_ROOT，模块导入时算死的值会跟不上
    return os.path.dirname(SKILLS_ROOT)


def _under_managed_base(path: str) -> bool:
    real = os.path.realpath(path)
    for base in (SKILLS_ROOT, _packages_base()):
        real_base = os.path.realpath(base)
        if real == real_base or real.startswith(real_base + os.sep):
            return True
    return False


def localize_root(path: str) -> str:
    """把配置里的资源 / 脚本目录换算成本机路径。

    以前导入时写的是导入那台机器上的绝对路径（如 D:\\PyCharm\\...\\skills_packages\\imported\\x\\resources），
    配置文件和资源一起带到 Linux 容器后，这个路径在容器里不存在——而且在 Linux 上它连“绝对路径”都不算，
    会被拼到 skills/ 下面，所有资源都显示“不存在”，技能被判为不可用。
    这里按路径里的 skills_packages/ 或 skills/ 那一段，换算到本机同样的位置；本机路径原样返回。
    只在读取时换算、不改写配置文件：配置原文照旧，直接读原文的地方（如 script_report.reanalyze_config）也要先过这里。
    """
    raw = str(path or "")
    if not raw or os.path.exists(raw):
        return raw
    if os.path.isabs(raw) and _under_managed_base(raw):   # 本机管理目录下、只是还没建出来的路径，不用换算
        return raw
    parts = [p for p in raw.replace("\\", "/").split("/") if p]
    for marker in _ROOT_MARKERS:
        if marker in parts:
            idx = len(parts) - 1 - parts[::-1].index(marker)
            return os.path.join(_project_root(), marker, *parts[idx + 1:])
    return raw


def _resolve_managed_root(path: str) -> str:
    """读取时：先换算成本机路径；以 skills_packages/ 或 skills/ 开头的相对路径按项目根目录解析，其余相对路径按 skills/ 解析（旧写法）。"""
    local = localize_root(path)
    if os.path.isabs(local):
        return local
    rel = local.replace("\\", "/")
    if rel.split("/")[0] in _ROOT_MARKERS:
        return os.path.join(_project_root(), *rel.split("/"))
    return _safe_join(SKILLS_ROOT, local)


def _ensure_managed_dir(path: str, label: str, bases: List[str]) -> str:
    """resource_root / scripts_root 只能指向平台自己管理的目录。

    用户可以上传 .yml 配置，如果不限制，里面写 scripts_root: /app 就能让脚本沙箱把项目根目录
    （含 .env）打包进去再读出来。realpath 同时挡住 ../ 和符号链接绕路。
    """
    real = os.path.realpath(path)
    for base in bases:
        real_base = os.path.realpath(base)
        if real == real_base or real.startswith(real_base + os.sep):
            return os.path.abspath(path)
    raise SkillValidationError(f"{label} 只能指向平台管理的目录，不允许: {path}")


def _normalize_permissions(raw: Any) -> Dict[str, Any]:
    permissions = raw if isinstance(raw, dict) else {}
    file_read = permissions.get("file_read") or []
    if not isinstance(file_read, list):
        raise SkillValidationError("permissions.file_read 必须是列表")
    return {
        "network": bool(permissions.get("network", False)),
        "file_read": [str(item).replace("\\", "/") for item in file_read if str(item).strip()],
        "exec": bool(permissions.get("exec", False)),
    }

def _normalize_resources(config: Dict[str, Any], file_path: str) -> Dict[str, Any]:
    resources_raw = config.get("resources") or []
    if not isinstance(resources_raw, list):
        raise SkillValidationError("resources 字段必须是列表")
    permissions = config["permissions"]
    allowed = set(permissions.get("file_read") or [])
    resource_root = config.get("resource_root") or os.path.join(os.path.dirname(file_path), "resources")
    resource_root = _resolve_managed_root(resource_root)
    resource_root = _ensure_managed_dir(resource_root, "resource_root", [SKILLS_ROOT, _packages_base()])
    normalized_resources: List[Dict[str, Any]] = []
    resource_text_parts: List[str] = []
    text_exts = {".txt", ".md", ".markdown", ".json", ".csv", ".yml", ".yaml"}
    for item in resources_raw:
        if isinstance(item, str):
            rel_path = item.replace("\\", "/")
            name = os.path.basename(rel_path)
        elif isinstance(item, dict) and item.get("path"):
            rel_path = str(item.get("path")).replace("\\", "/")
            name = str(item.get("name") or os.path.basename(rel_path))
        else:
            raise SkillValidationError(f"resources 项格式错误: {item}")
        abs_path = _safe_join(resource_root, rel_path)
        exists = os.path.exists(abs_path)
        size = os.path.getsize(abs_path) if exists and os.path.isfile(abs_path) else 0
        normalized_resources.append({
            "name": name,
            "path": rel_path,
            "exists": exists,
            "size": size,
            "allowed": rel_path in allowed,
        })
        if rel_path in allowed and exists and os.path.isfile(abs_path):
            ext = os.path.splitext(abs_path.lower())[1]
            if ext in text_exts and size <= 200_000:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                resource_text_parts.append(f"【资源: {name}】\n{content[:12000]}")
    missing_allowed = sorted(path for path in allowed if not os.path.exists(_safe_join(resource_root, path)))
    if missing_allowed:
        raise SkillValidationError(f"permissions.file_read 包含不存在的资源: {', '.join(missing_allowed)}")
    return {
        "resource_root": resource_root,
        "resources": normalized_resources,
        "resource_text": "\n\n".join(resource_text_parts),
    }

def load_skill_config(config_file:str)->Dict[str,Any]:
    """加载并校验Skill的YML配置
      config_file: Skill.config_file 存的文件名
        :return: 结构化的配置dict，字段：
            {"name": str,
            "description": str,
            "version": str,
            "tools": [{"name": str, "defaults": dict}],
            "system_prompt": str,
            "tool_defaults_map": {tool_name: defaults_dict},  # 方便运行时快速查
            "tool_names": [tool_name1, tool_name2],          # 工具名列表，ToolExecutor过滤用}
        :raises SkillValidationError: 配置格式错误"""
    file_path = _get_yml_path(config_file)#取绝对路径
    mtime = os.path.getmtime(file_path) if os.path.exists(file_path) else 0
    return skill_cache.get_or_set(("skill_config", config_file, mtime), lambda: _load_skill_config_uncached(config_file, file_path))


def invalidate_skill_config(config_file: str = None):
    if config_file:
        skill_cache.invalidate(prefix=("skill_config", config_file))
    else:
        skill_cache.invalidate(prefix=("skill_config",))


def _load_skill_config_uncached(config_file: str, file_path: str)->Dict[str,Any]:
    import service.tools  # noqa: F401 - ensure built-in tools are registered before validation
    #1，读文件
    if not os.path.exists(file_path):
        raise SkillValidationError(f"Skill配置文件不存在: {file_path}")
    try:
        with open(file_path,"r",encoding="utf-8") as f:
            config = yaml.safe_load(f)#把YAML文件中的配置内容读取出来，并转换成Python对象
    except yaml.YAMLError as e:
        raise SkillValidationError(f"Skill配置YAML解析失败: {e}\n文件: {file_path}")
    if not isinstance(config, dict):
        raise SkillValidationError(f"Skill配置格式错误：根节点必须是字典，实际是{type(config)}\n文件: {file_path}")
    #2,必填字段
    required_fields = ["name","tools"]
    for field in required_fields:
        if field not in config:
            raise SkillValidationError(f"Skill配置缺少必填字段: {field}\n文件: {file_path}")
    #3,填补默认值
    config.setdefault("description","")
    config.setdefault("version", "1.0")
    config.setdefault("system_prompt", "")
    config["permissions"] = _normalize_permissions(config.get("permissions"))
    if config["permissions"].get("exec"):
        raise SkillValidationError("Skill 不允许声明 exec=true")
    resource_info = _normalize_resources(config, file_path)
    config["resource_root"] = resource_info["resource_root"]
    config["resources"] = resource_info["resources"]
    config["resource_text"] = resource_info["resource_text"]
    # 脚本包（导入 Skill 时生成；实际执行在沙箱里，见 service/sandbox.py）
    if config.get("scripts_root") or config.get("scripts"):
        scripts_root = config.get("scripts_root")
        scripts_raw = config.get("scripts")
        if not scripts_root or not isinstance(scripts_raw, list):
            raise SkillValidationError("scripts_root 和 scripts 必须同时提供，且 scripts 是列表")
        config["scripts_root"] = _ensure_managed_dir(_resolve_managed_root(str(scripts_root)), "scripts_root", [_packages_base()])
        scripts: List[str] = []
        for item in scripts_raw:
            rel = str(item).replace("\\", "/")
            if not rel.endswith(".py"):
                raise SkillValidationError(f"scripts 里只能是 .py 文件: {item}")
            _safe_join(config["scripts_root"], rel)
            scripts.append(rel)
        config["scripts"] = scripts
        runnable_raw = config.get("runnable_scripts")
        if runnable_raw is not None:
            if not isinstance(runnable_raw, list) or any(str(r).replace("\\", "/") not in scripts for r in runnable_raw):
                raise SkillValidationError("runnable_scripts 必须是 scripts 的子集")
            config["runnable_scripts"] = [str(r).replace("\\", "/") for r in runnable_raw]
        if not isinstance(config.get("script_report"), dict):
            config.pop("script_report", None)
    #4，tools字段校验+规范化
    tools_raw = config["tools"]
    if not isinstance(tools_raw,list):
        raise SkillValidationError(f"Skill的tools字段必须是列表，实际是{type(tools_raw)}")
    normalized_tools=[]
    tool_defaults_map:Dict[str,Dict[str,Any]]={}
    tool_names:List[str]=[]
    for item in tools_raw:
        if isinstance(item,str):
            tool_name = item
            defaults ={}
        elif isinstance(item,dict) and "name" in item:
            tool_name = item["name"]
            defaults = item.get("defaults", {}) or {}#如果有 defaults，拿出来；没有就使用 {}。
        else:
            raise SkillValidationError(
                f"Skill的tools列表元素格式错误: {item}\n"
                f"支持两种格式:\n"
                f"  - name: outline_generator\n"
                f"    defaults: {{sections: 6}}\n"
                f"  或简写:\n"
                f"  - word_count"
            )
        # 校验工具名是否在 ToolRegistry 中存在
        from service.tools.base import ToolRegistry
        if ToolRegistry.get(tool_name) is None:
            available = ToolRegistry.list_all()
            logger.warning(
                f"Skill '{config['name']}' 引用了未实现的工具 '{tool_name}'，"
                f"已跳过。可用工具: {available}"
            )
            continue  # 未实现的工具跳过，不阻塞运行
        normalized_tools.append({"name": tool_name, "defaults": defaults})
        tool_defaults_map[tool_name] = defaults
        tool_names.append(tool_name)
    config["tools"]=normalized_tools
    config["tool_defaults_map"]=tool_defaults_map
    config["tool_names"]=tool_names
    logger.info(f"加载Skill配置成功: {config['name']} (工具{len(tool_names)}个, 资源{len(config['resources'])}个)")
    return config
def list_available_templates()->List[str]:
    #列出内置模板和用户自定义模板；不把 imported/user_created 当模板列出。
    if not os.path.exists(SKILLS_ROOT):
        return []
    templates = [
        f for f in os.listdir(SKILLS_ROOT)
        if os.path.isfile(os.path.join(SKILLS_ROOT, f)) and f.endswith((".yml", ".yaml"))
    ]
    user_templates_root = os.path.join(SKILLS_ROOT, "user_templates")
    if os.path.exists(user_templates_root):
        for root, _, files in os.walk(user_templates_root):
            for filename in files:
                if filename.endswith((".yml", ".yaml")):
                    full_path = os.path.join(root, filename)
                    templates.append(os.path.relpath(full_path, SKILLS_ROOT).replace("\\", "/"))
    return sorted(templates)
def get_template_path(filename: str) -> str:
   #直接返回文件名（config_file 存的就是文件名）
    return filename
