"""Skill 与 Agent 的绑定/解绑、合并配置（ToolExecutor 运行时依赖此模块）。"""
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from service.access_control import can_bind_skill, can_read_skill, get_owned_agent, get_usable_agent
from service.lifecycle import BINDABLE_BY_OTHERS_STATUSES, RETIRED_STATUS
from models.agent_dao import get_agent_by_id
from models.skill_dao import (
    bind_skill_to_agent as dao_bind,
    unbind_skill_from_agent as dao_unbind,
    get_skill_by_id as dao_get,
    list_skills_by_agent as dao_list_by_agent,
    unbind_all_skills_from_agent as dao_unbind_all,
)
from service import sandbox
from service.skills.loader import load_skill_config
from utils.logger_handler import get_logger

from .common import _skill_to_dict

logger = get_logger("skill_service")


NO_EXEC_NOTE = (
    "【运行环境说明】本平台的助手无法运行脚本或命令，也不能读取本地文件。"
    "下文如果要求运行脚本、执行命令或读取文件，请改为直接用文字完成，"
    "或明确告诉用户这一步需要他在自己的电脑上执行。"
)


def _script_section(name: str, scripts: List[str]) -> str:
    listed = "、".join(scripts[:30]) + (f" 等共 {len(scripts)} 个" if len(scripts) > 30 else "")
    return (
        f"【运行环境说明】这个 Skill 的脚本可以用 run_skill_script 工具在隔离沙箱里运行（skill 填「{name}」）。\n"
        f"可运行的脚本：{listed}\n"
        "- 下文让你运行某个脚本时，就调用这个工具，不要假装已经运行过。\n"
        "- 用户上传的文件会以附件 ID 出现在消息里，用 input_files 传入；脚本里的路径是 inputs/文件名。\n"
        "- 脚本生成的文件写到 outputs/ 目录；工具返回的下载链接要原样放进回答里给用户。\n"
        "- 沙箱没有网络，不能安装依赖，只有常见的 PDF / Excel / Word / pandas 等库。"
    )


def bind_skill(db: Session, agent_id: int, skill_id: int, user_id: int) -> bool:
    """绑定Skill到Agent（校验：Skill必须是当前用户创建的或公开的）"""
    agent = get_owned_agent(db, user_id, agent_id)
    if not agent:
        logger.warning(f"权限拒绝：用户{user_id}无权操作Agent {agent_id}")
        return False
    skill = dao_get(db, skill_id)
    if not skill:
        return False
    # 权限：自己创建的 / 公开的 / 部门或企业共享给自己的 Skill 才能绑定
    if not can_read_skill(skill, user_id, db):
        logger.warning(f"权限拒绝：用户{user_id}无权绑定Skill {skill_id}")
        return False
    # 发布状态：别人的 Skill 必须已发布才能绑定，草稿/待审核/已退役的只有作者自己能绑
    # （给自己的测试 Agent 用）。
    if not can_bind_skill(skill, user_id):
        logger.warning(f"绑定拒绝：Skill {skill_id} 还未发布（{skill.lifecycle_status}），用户{user_id}不是作者")
        return False
    success = dao_bind(db, agent_id, skill_id)
    if success:
        db.commit()
    return success


def unbind_skill(db: Session, agent_id: int, skill_id: int, user_id: int) -> bool:
    """解绑Skill（校验：Agent必须是当前用户的）"""
    agent = get_owned_agent(db, user_id, agent_id)
    if not agent:
        return False
    success = dao_unbind(db, agent_id, skill_id)
    if success:
        db.commit()
    return success


def list_agent_skills(db: Session, agent_id: int, user_id: int = None) -> List[Dict]:
    """只读列表，用 `get_usable_agent`：部门/企业共享的 Agent 也能看它绑了哪些 Skill。
    改绑定关系（`bind_skill`/`unbind_skill`/`update_agent_skills`）继续用
    `get_owned_agent`，看跟改不是一回事。"""
    if user_id is not None:
        agent = get_usable_agent(db, user_id, agent_id)
        if not agent:
            logger.warning(f"权限拒绝：用户{user_id}无权查看Agent {agent_id}的Skill")
            return []
    skills = dao_list_by_agent(db, agent_id)
    return [_skill_to_dict(s) for s in skills]


def update_agent_skills(db: Session, agent_id: int, skill_ids: List[int], user_id: int = None, commit: bool = True) -> bool:
    """批量更新Agent绑定的Skill（先全部解绑，再绑定新的）"""
    if user_id is not None:
        agent = get_owned_agent(db, user_id, agent_id)
        if not agent:
            logger.warning(f"权限拒绝：用户{user_id}无权操作Agent {agent_id}")
            return False
        for skill_id in skill_ids:
            skill = dao_get(db, skill_id)
            if not can_read_skill(skill, user_id, db):
                logger.warning(f"权限拒绝：用户{user_id}无权绑定Skill {skill_id}")
                return False
            if not can_bind_skill(skill, user_id):
                logger.warning(f"绑定拒绝：Skill {skill_id} 还未发布，用户{user_id}不是作者")
                return False
    dao_unbind_all(db, agent_id)
    for skill_id in skill_ids:
        dao_bind(db, agent_id, skill_id)
    logger.info(f"更新Agent绑定Skill: agent={agent_id}, skills={skill_ids}")
    if commit:
        db.commit()
    return True


def get_agent_skills_merged_config(db: Session, agent_id: int) -> Dict[str, Any]:
    """加载Agent绑定的所有Skill配置并合并ToolExecutor运行时调用：
      1. 查Agent绑定的所有Skill
      2. 逐个加载YML配置（调loader）
      3. 合并工具列表（并集）
      4. 合并工具默认值（同名的以先绑定的为准，不覆盖）
      5. 合并system_prompt（所有Skill的prompt拼接）
    :return: {
        "skill_names": ["论文写作助手", ...],
        "tool_names": ["outline_generator", "word_count", ...],  # 工具并集
        "tool_defaults_map": {"outline_generator": {"sections": 6}, ...},
        "system_prompt": "合并后的Skill prompt",
        "skills": [每个Skill的完整配置],
    }
    """
    skills = dao_list_by_agent(db, agent_id)
    if not skills:
        return {
            "skill_names": [],
            "tool_names": [],
            "tool_defaults_map": {},
            "system_prompt": "",
            "skills": [],
            "permissions": {"network": False, "file_read": [], "exec": False},
            "resource_roots": [],
            "resources": [],
            "skill_bundles": {},
        }
    merged_tool_names: List[str] = []
    merged_defaults: Dict[str, Dict] = {}
    skill_prompts: List[str] = []
    skill_configs: List[Dict] = []
    resource_roots: List[str] = []
    resources: List[Dict[str, Any]] = []
    load_errors: List[Dict[str, str]] = []
    skill_bundles: Dict[str, Dict[str, Any]] = {}
    sandbox_on = sandbox.is_enabled()
    agent = get_agent_by_id(db, agent_id)
    agent_owner_id = agent.user_id if agent else None
    for skill in skills:
        if skill.lifecycle_status == RETIRED_STATUS:
            # 退役即彻底停用：哪怕是作者自己的 Agent 也不再加载——跟"草稿能被作者
            # 自己拿来测试"不是一回事，退役是"不管是谁都不该再用"。已经绑定的关系
            # 不用跟着解绑（后台随时能重新发布，解绑了还要重新配一遍），只是运行时
            # 跳过。
            logger.info(f"跳过已退役Skill: agent={agent_id}, skill={skill.name}")
            continue
        if skill.user_id != agent_owner_id and skill.lifecycle_status not in BINDABLE_BY_OTHERS_STATUSES:
            # 绑定时的 can_bind_skill 只保证了"绑定那一刻"是已发布状态——如果作者
            # 之后把它改回草稿/待审核（比如发现问题想先撤回），已经绑定它的别人的
            # Agent 不能继续用旧配置跑下去，运行时要跟着重新检查，不能只信一次性
            # 检查过的绑定关系。Agent 自己作者绑自己的 Skill 不受这条限制（那是
            # "拿自己的草稿喂自己的测试 Agent"，跟上面 RETIRED 的例外是同一个道理）。
            logger.info(
                f"跳过未发布的共享Skill: agent={agent_id}, skill={skill.name}, "
                f"status={skill.lifecycle_status}"
            )
            continue
        try:
            cfg = load_skill_config(skill.config_file)
        except Exception as e:
            logger.error(f"加载Skill配置失败: skill={skill.name}, error={e}")
            load_errors.append({"skill_name": skill.name, "error": str(e)})
            continue
        skill_configs.append(cfg)
        if cfg.get("resource_root") and cfg.get("resource_root") not in resource_roots:
            resource_roots.append(cfg.get("resource_root"))
        for resource in cfg.get("resources", []):
            item = dict(resource)
            item["skill_name"] = cfg.get("name")
            resources.append(item)
        # 工具并集（保持顺序，不重复）
        for name in cfg["tool_names"]:
            if name not in merged_tool_names:
                merged_tool_names.append(name)
        # 默认值合并（同名的以先绑定的为准，不覆盖）
        for name, defaults in cfg["tool_defaults_map"].items():
            if name not in merged_defaults:
                merged_defaults[name] = defaults
        # 带脚本的 Skill：沙箱开着就告诉模型怎么跑，否则老实说跑不了
        env_note = ""
        # 只把静态检查通过的脚本交给助手；老配置没有检查结果时退回全部脚本
        scripts = cfg.get("runnable_scripts") if cfg.get("runnable_scripts") is not None else (cfg.get("scripts") or [])
        if scripts and sandbox_on and cfg.get("scripts_root"):
            skill_bundles[cfg["name"]] = {"root": cfg["scripts_root"], "scripts": list(scripts)}
            env_note = _script_section(cfg["name"], scripts)
            if "run_skill_script" not in merged_tool_names:
                merged_tool_names.append("run_skill_script")
        elif cfg.get("origin") == "official":
            env_note = NO_EXEC_NOTE
        # prompt拼接
        if cfg["system_prompt"]:
            body = f"{env_note}\n\n{cfg['system_prompt']}" if env_note else cfg["system_prompt"]
            skill_prompts.append(f"【Skill: {cfg['name']}】\n{body}")
        if cfg.get("resource_text"):
            skill_prompts.append(f"【Skill资源: {cfg['name']}】\n{cfg['resource_text']}")
    merged_prompt = "\n\n".join(skill_prompts) if skill_prompts else ""
    logger.info(
        f"合并Agent Skill配置: agent={agent_id}, "
        f"skills={len(skill_configs)}个, tools={len(merged_tool_names)}个"
        + (f", 加载失败={len(load_errors)}个" if load_errors else "")
    )
    return {
        "skill_names": [cfg["name"] for cfg in skill_configs],
        "tool_names": merged_tool_names,
        "tool_defaults_map": merged_defaults,
        "system_prompt": merged_prompt,
        "skills": skill_configs,
        "permissions": {
            "network": any(bool(cfg.get("permissions", {}).get("network")) for cfg in skill_configs),
            "file_read": [
                path
                for cfg in skill_configs
                for path in cfg.get("permissions", {}).get("file_read", [])
            ],
            "exec": False,
        },
        "resource_roots": resource_roots,
        "resources": resources,
        "skill_bundles": skill_bundles,
        "load_errors": load_errors,
    }
