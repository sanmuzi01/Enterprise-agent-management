from typing import List, Optional,Dict,Any
from sqlalchemy.exc import IntegrityError
from prompt.prompt_manager import create_prompt_file, read_prompt_file, update_prompt_file, delete_prompt_file
from models.agent_dao import get_agent_by_id, list_agents_by_user, create_agent, update_agent, get_selected_agent_by_user
from models.user_dao import update_selected_agent
from models.init_db import Agent
from sqlalchemy.exc import SQLAlchemyError
from service.prompt_guard import UNTRUSTED_RULE
from utils.logger_handler import get_logger
from fastapi import HTTPException, status
logger = get_logger("agent_service")
# 1. 获取某用户的全部智能体列表（带选中标记）
def list_agent(db,user)->List[Dict[str,Any]]:
    """
    查询用户的智能体列表，每个智能体附带 is_selected 标记
    :param db: 数据库会话
    :param user: 当前用户对象（由 get_current_user 得到）
    :return: 字典列表，每个元素包含 agent 字段 + is_selected
    """
    agents : List[Agent] = list_agents_by_user(db,user.id)
    selected_id = user.selected_agent_id
    from models.agent_knowledge_space_dao import list_space_ids_by_agent
    result = []
    for agent in agents:
        prompt = read_prompt_file(agent.id)
        # 查询Agent绑定的Skill列表
        from service.skill_service import list_agent_skills
        skills = list_agent_skills(db, agent.id)
        result.append({
            "id": agent.id,
            "name": agent.name,
            "prompt": prompt,
            "model_name": agent.model_name,
            "rag_enabled": agent.rag_enabled,
            "memory_enabled": agent.memory_enabled,
            "temperature": agent.temperature,
            "skills": skills,
            "space_ids": list_space_ids_by_agent(db, agent.id),
            "kb_top_k": agent.kb_top_k,
            "kb_rerank_enabled": agent.kb_rerank_enabled,
            "kb_force_citation": agent.kb_force_citation,
            "kb_refuse_when_empty": agent.kb_refuse_when_empty,
            "is_selected": (agent.id == selected_id)
        })
    return result

#获取单个智能体信息(验证归属）
def get_agent(db,user,agent_id:int)->Optional[Dict[str,Any]]:
    """查询单个智能体，必须是当前用户的，否则返回 None（防止越权）"""
    agent = get_agent_by_id(db, agent_id)
    if not agent or agent.user_id != user.id:
        return None
    from service.skill_service import list_agent_skills
    from models.agent_knowledge_space_dao import list_space_ids_by_agent
    skills = list_agent_skills(db, agent.id)
    return {
        "id": agent.id,
        "name": agent.name,
        "prompt": read_prompt_file(agent.id),
        "model_name": agent.model_name,
        "rag_enabled": agent.rag_enabled,
        "memory_enabled": agent.memory_enabled,
        "temperature": agent.temperature,
        "skills": skills,
        "space_ids": list_space_ids_by_agent(db, agent.id),
        "kb_top_k": agent.kb_top_k,
        "kb_rerank_enabled": agent.kb_rerank_enabled,
        "kb_force_citation": agent.kb_force_citation,
        "kb_refuse_when_empty": agent.kb_refuse_when_empty,
        "is_selected": (agent.id == user.selected_agent_id)
    }
def _validate_space_ids(db, user_id: int, space_ids) -> Optional[str]:
    """校验 space_ids 都属于当前用户（唯一隔离入口 access_control.user_space_ids）。"""
    if not space_ids:
        return None
    from service.access_control import user_space_ids
    allowed = user_space_ids(db, user_id)
    bad = [int(s) for s in space_ids if int(s) not in allowed]
    if bad:
        return f"包含无权访问的知识库空间：{bad}"
    return None


#创建智能体（创建后为自动选中）
def create(db,user,name:str,role:str = None, task: str = None,
           constraints: str = None, output: str = None,
           model_name: str = "glm-4",
           rag_enabled: int = 0, memory_enabled: int = 1,
           temperature: int = 70,
           skill_ids: Optional[List[int]] = None,
           space_ids: Optional[List[int]] = None,
           kb_top_k: int = None, kb_rerank_enabled: int = None,
           kb_force_citation: int = None, kb_refuse_when_empty: int = None,
           agent_type: str = None, department_code: str = None)->Dict[str,Any]:
    """创建智能体，创建后自动选中。

    agent_type/department_code 是 Phase 3D 阶段3（中央 Agent 受控路由）用的，
    不传就是现存的 personal/NULL，不影响任何既有创建流程。"""
    space_err = _validate_space_ids(db, user.id, space_ids)
    if space_err:
        return {"message": space_err}
    from service.runtime.central_router import VALID_AGENT_TYPES, VALID_DEPARTMENT_CODES
    if agent_type is not None and agent_type not in VALID_AGENT_TYPES:
        return {"message": f"agent_type 只能是 {sorted(VALID_AGENT_TYPES)} 之一"}
    if department_code is not None and department_code not in VALID_DEPARTMENT_CODES:
        return {"message": f"department_code 只能是 {sorted(VALID_DEPARTMENT_CODES)} 之一"}
    try:
        agent =create_agent(
            db=db, name=name, user_id=user.id,
            prompt_file=None,
            model_name=model_name, rag_enabled=rag_enabled,
            memory_enabled=memory_enabled,
            temperature=temperature,
            kb_top_k=kb_top_k, kb_rerank_enabled=kb_rerank_enabled,
            kb_force_citation=kb_force_citation, kb_refuse_when_empty=kb_refuse_when_empty,
            agent_type=agent_type, department_code=department_code,
        )
        # 始终创建提示词 yml 文件（即使字段为空，保证每个 Agent 都有 prompt 文件）
        prompt_path = create_prompt_file(agent.id, role, task, constraints, output)
        agent.prompt_file = prompt_path
        db.flush()
        # 创建后自动设为当前选中
        update_selected_agent(db, user, agent.id)
        db.flush()
        if skill_ids:
            from service.skill_service import update_agent_skills
            if not update_agent_skills(db, agent.id, skill_ids, user_id=user.id, commit=False):
                db.rollback()
                return {"message": "绑定Skill失败，请检查Skill是否存在或有权限"}
        if space_ids is not None:
            from models.agent_knowledge_space_dao import set_agent_spaces
            set_agent_spaces(db, agent.id, space_ids, commit=False)
        db.commit()
        return {
            "message": "创建成功",
            "agent_id": agent.id,
            "name": agent.name
        }
    except IntegrityError:
        db.rollback()
        return {
            "message": "创建失败，智能体名称已存在"
        }
    except SQLAlchemyError as e:
        db.rollback()
        raise e


def clone(db, user, agent_id: int, name: str = None) -> Optional[Dict[str, Any]]:
    """复制一个已有 Agent 的配置，生成新的 Agent。"""
    source = get_agent_by_id(db, agent_id)
    if not source or source.user_id != user.id:
        return None

    prompt = read_prompt_file(source.id) or {}
    clone_name = (name or f"{source.name} 副本").strip()
    result = create(
        db=db,
        user=user,
        name=clone_name,
        role=prompt.get("role"),
        task=prompt.get("task"),
        constraints=prompt.get("constraints"),
        output=prompt.get("output"),
        model_name=source.model_name,
        rag_enabled=source.rag_enabled,
        memory_enabled=source.memory_enabled,
        temperature=source.temperature,
    )
    if "agent_id" not in result:
        return result

    from service.skill_service import list_agent_skills, update_agent_skills

    skill_ids = [skill["id"] for skill in list_agent_skills(db, source.id, user_id=user.id)]
    if skill_ids:
        update_agent_skills(db, result["agent_id"], skill_ids, user_id=user.id)
    db.commit()
    logger.info(f"克隆 Agent 成功: source={source.id}, cloned={result['agent_id']}, user={user.id}")
    return {
        "message": "克隆成功",
        "agent_id": result["agent_id"],
        "name": clone_name,
        "source_agent_id": source.id,
    }
# 4. 更新智能体（只能改自己的）
def update(db, user, agent_id: int, name: str = None,role: str = None,
           task: str = None, constraints: str = None, output: str = None,
           model_name: str = None, rag_enabled: int = None,
           memory_enabled: int = None, temperature: int = None,
           skill_ids: Optional[List[int]] = None,
           space_ids: Optional[List[int]] = None,
           kb_top_k: int = None, kb_rerank_enabled: int = None,
           kb_force_citation: int = None, kb_refuse_when_empty: int = None) -> Dict[str, Any]:
    """更新智能体，先验证归属。space_ids=None 不改绑定，[] 清空绑定。"""
    agent = get_agent_by_id(db, agent_id)
    if not agent or agent.user_id != user.id:
        return {"message": "智能体不存在或不属于当前用户"}
    space_err = _validate_space_ids(db, user.id, space_ids)
    if space_err:
        return {"message": space_err}
    try:
        if role is not None or task is not None or constraints is not None or output is not None:
            existing = read_prompt_file(agent.id)
            if existing is not None:
                update_prompt_file(agent_id, role or existing.get("role"),
                    task or existing.get("task"),
                    constraints or existing.get("constraints"),
                    output or existing.get("output"))
            else:
                prompt_path = create_prompt_file(agent.id,role, task, constraints, output)
                agent.prompt_file=prompt_path
                db.flush()

        agent = update_agent(
            db=db, agent=agent, name=name,
            model_name=model_name,
            rag_enabled=rag_enabled,
            memory_enabled=memory_enabled,
            temperature=temperature,
            kb_top_k=kb_top_k, kb_rerank_enabled=kb_rerank_enabled,
            kb_force_citation=kb_force_citation, kb_refuse_when_empty=kb_refuse_when_empty,
        )
        if skill_ids is not None:
            from service.skill_service import update_agent_skills
            if not update_agent_skills(db, agent_id, skill_ids, user_id=user.id, commit=False):
                db.rollback()
                return {"message": "绑定Skill失败，请检查Skill是否存在或有权限"}
        if space_ids is not None:
            from models.agent_knowledge_space_dao import set_agent_spaces
            set_agent_spaces(db, agent_id, space_ids, commit=False)
        db.commit()
        return {"message":"更新成功","agent_id":agent_id}
    except SQLAlchemyError as e:
        db.rollback()
        raise e
# 4.5. 删除预检（返回将被连带删除的数量，供前端二次确认）
def delete_preview(db, user, agent_id: int) -> Optional[Dict[str, Any]]:
    """返回删除该 Agent 会波及的数据量；无权限/不存在时返回 None"""
    from models.agent_dao import get_agent_by_id
    from models.conversation_dao import list_conversations_by_agent
    from models.init_db import BackgroundTask, Knowledge, AgentRun

    agent = get_agent_by_id(db, agent_id)
    if not agent or agent.user_id != user.id:
        return None

    conversations = list_conversations_by_agent(db, user.id, agent.id)
    conversation_count = len(conversations)
    # 会话下的消息数
    message_count = sum(len(c.messages or []) for c in conversations)
    # Skill 绑定数（agent_skill 是 Table 不是类，所以用 db.execute(select(...).count()) 不行，直接用 text 查询更简单）
    from sqlalchemy import text
    skill_count = db.execute(
        text("SELECT COUNT(*) FROM agent_skill WHERE agent_id = :aid"),
        {"aid": agent.id}
    ).scalar() or 0
    # 知识库文档数
    knowledge_count = db.query(Knowledge).filter(Knowledge.agent_id == agent.id).count()
    # 运行记录数
    run_count = db.query(AgentRun).filter(AgentRun.agent_id == agent.id, AgentRun.user_id == user.id).count()
    # 后台任务数
    task_count = db.query(BackgroundTask).filter(
        BackgroundTask.agent_id == agent.id,
        BackgroundTask.user_id == user.id
    ).count()

    return {
        "agent_id": agent.id,
        "agent_name": agent.name,
        "conversation_count": conversation_count,
        "message_count": message_count,
        "skill_binding_count": skill_count,
        "knowledge_count": knowledge_count,
        "run_count": run_count,
        "task_count": task_count,
        "total_impacted": conversation_count + message_count + knowledge_count + run_count + task_count,
    }
## 5. 删除智能体（只能删自己的，按顺序显式级联）
def _remove_private_skill_file(config_file: str) -> None:
    """数据库提交成功后再删磁盘文件；文件删不掉只记日志（数据体检会把它列成孤儿文件，可以事后清理）。"""
    import os
    from service.skills import loader as skill_loader
    path = skill_loader._get_yml_path(config_file)
    try:
        if os.path.exists(path):
            os.remove(path)
        skill_loader.invalidate_skill_config(config_file)
    except OSError as exc:
        logger.warning(f"删除专业技能配置文件失败: {path}: {exc}")


def delete(db, user, agent_id: int) -> Dict[str, Any]:
    """删除顺序：会话 → 运行轨迹 → 后台任务 → 知识库块/向量 → 记忆/工具/旧聊天 → 提示词 → Agent"""
    from models.agent_dao import get_agent_by_id, delete_agent as dao_delete_agent
    from models.conversation_dao import list_conversations_by_agent, delete_conversation as dao_delete_conversation
    from models.init_db import BackgroundTask, Chat, Knowledge, KnowledgeChunk, Memory, Tool, AgentRun
    from service.rag.vector_store_service import delete_collection
    from sqlalchemy import text

    agent = get_agent_by_id(db, agent_id)
    if not agent or agent.user_id != user.id:
        return {"message": "智能体不存在或无权限删除"}
    try:
        # 1. 如果删的是当前选中的，清空用户 selected_agent_id
        if user.selected_agent_id == agent.id:
            update_selected_agent(db, user, None)

        # 2. 删除会话 + 级联 Message（conversation relationship cascade=all,delete-orphan 自动删 message）
        conversations = list_conversations_by_agent(db, user.id, agent.id)
        for conv in conversations:
            dao_delete_conversation(db, conv)

        # 3. 删除 AgentRun 的子表：AgentStep（必须先清 Step，因为 step.run_id 外键引用 run.id）
        db.execute(
            text("""
                DELETE agent_step FROM agent_step
                INNER JOIN agent_run ON agent_step.run_id = agent_run.id
                WHERE agent_run.agent_id = :aid AND agent_run.user_id = :uid
            """),
            {"aid": agent.id, "uid": user.id}
        )

        # 4. 再删 AgentRun 记录
        db.query(AgentRun).filter(
            AgentRun.agent_id == agent.id,
            AgentRun.user_id == user.id
        ).delete(synchronize_session=False)

        # 5. 删除后台任务记录
        db.query(BackgroundTask).filter(
            BackgroundTask.agent_id == agent.id,
            BackgroundTask.user_id == user.id
        ).delete(synchronize_session=False)

        # 6. 删除知识库向量集合、文档块和文档记录
        try:
            delete_collection(agent.id)
        except Exception as e:
            logger.warning(f"删除 Agent 向量集合失败，继续删除DB记录: {e}")
        knowledge_ids = [row[0] for row in db.query(Knowledge.id).filter(Knowledge.agent_id == agent.id).all()]
        if knowledge_ids:
            db.query(KnowledgeChunk).filter(KnowledgeChunk.knowledge_id.in_(knowledge_ids)).delete(synchronize_session=False)
        db.query(Knowledge).filter(Knowledge.agent_id == agent.id).delete(synchronize_session=False)

        # 7. 删除 Agent 相关记忆、工具和旧版聊天记录
        db.query(Memory).filter(Memory.agent_id == agent.id, Memory.user_id == user.id).delete(synchronize_session=False)
        db.query(Tool).filter(Tool.agent_id == agent.id).delete(synchronize_session=False)
        db.query(Chat).filter(Chat.agent_id == agent.id, Chat.user_id == user.id).delete(synchronize_session=False)

        # 8. 清理提示词文件
        delete_prompt_file(agent_id)

        # 9. 最后删 Agent（Skill 绑定 agent_skill 由 Agent 的 relationship cascade 自动删）
        dao_delete_agent(db, agent)

        # 10. 企业助手自己那份“专业业务技能”（每个助手单独一份，见 agent_admin_service.bind_template_skill）：
        #     只解绑不删，会留下没有主人的技能记录和 skills/enterprise/agent_<id>.yml——数据体检在开发库里查出过几十个。
        private_config = f"enterprise/agent_{agent_id}.yml"
        private_skills = [r[0] for r in db.execute(text("SELECT id FROM skill WHERE config_file = :f"), {"f": private_config}).all()]
        for skill_id in private_skills:
            db.execute(text("DELETE FROM skill_version WHERE skill_id = :s"), {"s": skill_id})
            db.execute(text("DELETE FROM agent_skill WHERE skill_id = :s"), {"s": skill_id})
            db.execute(text("DELETE FROM skill WHERE id = :s"), {"s": skill_id})

        # ========== 关键：成功路径一定要 commit ==========
        db.commit()
        _remove_private_skill_file(private_config)
        logger.info(f"删除 Agent 成功: agent_id={agent_id}, user_id={user.id}")
        return {"message": "删除成功", "agent_id": agent_id}

    except IntegrityError as e:
        db.rollback()
        err_msg = str(e.orig) if hasattr(e, 'orig') else str(e)
        logger.warning(f"删除 Agent {agent_id} 外键约束失败: {err_msg}")
        import re
        m = re.search(r'CONSTRAINT `(.+?)` FOREIGN KEY', err_msg)
        detail = "删除失败：仍有关联数据未清理"
        if m:
            detail += f"（受阻：{m.group(1)}）"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail
        )
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"删除 Agent {agent_id} 数据库异常: {e}")
        raise e
# 6. 选中某个智能体
def select(db,user,agent_id:int)->Optional[Dict[str,Any]]:
     """将某个智能体设为当前选中；必须是自己的"""
     agent = get_agent_by_id(db,agent_id)
     if not agent or agent.user_id != user.id:
         return {"message":"智能体不存在或无权限选中"}
     update_selected_agent(db, user, agent.id)
     db.commit()
     return {"message":"选中成功","agent_id":agent_id}

# 7. 获取当前选中的智能体详情
def get_selected(db,user)->Optional[Dict[str,Any]]:
    """获取当前选中的智能体详情"""
    agent = get_selected_agent_by_user(db, user)
    if not agent:
        return None
    from service.skill_service import list_agent_skills
    skills = list_agent_skills(db, agent.id)
    return {
        "id": agent.id,
        "name": agent.name,
        "prompt": read_prompt_file(agent.id),
        "model_name": agent.model_name,
        "rag_enabled": agent.rag_enabled,
        "memory_enabled": agent.memory_enabled,
        "temperature": agent.temperature,
        "skills": skills

    }


def get_agent_debug(db, user, agent_id: int) -> Optional[Dict[str, Any]]:
    """返回 Agent 运行前的可观测配置，供前端调试页使用。"""
    from models.knowledge_dao import list_knowledge_by_agent
    from prompt.prompt_manager import build_prompt, read_prompt_file
    from service.llm.llm_config_service import get_api_config
    from service.skill_service import get_agent_skills_merged_config
    from service.user_profile_service import format_user_profile_for_prompt

    agent = get_agent_by_id(db, agent_id)
    if not agent or agent.user_id != user.id:
        return None

    prompt_data = read_prompt_file(agent.id) or {}
    base_prompt = build_prompt(agent.id) or "你是一个通用智能助理。"
    profile_prompt = format_user_profile_for_prompt(db, user.id)
    skill_config = get_agent_skills_merged_config(db, agent.id)
    skill_prompt = skill_config.get("system_prompt", "")
    final_prompt = "\n\n".join([part for part in [base_prompt, profile_prompt, skill_prompt] if part])
    docs = list_knowledge_by_agent(db, agent.id)
    done_docs = [doc for doc in docs if doc.status == "done" and doc.chunk_count > 0]
    embedding_configured = any(
        get_api_config(db, user.id, model_name) is not None
        for model_name in ("embedding-3", "embedding-2", "text-embedding-3-small", "text-embedding-3-large", "glm-4")
    )

    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "model_name": agent.model_name,
            "temperature": agent.temperature,
            "rag_enabled": agent.rag_enabled,
            "memory_enabled": agent.memory_enabled,
        },
        "readiness": {
            # 企业助手（中央 / 部门）用企业管理员配置的模型连接，员工不用自己配（见 llm_config_service.use_agent_credentials）
            "model_configured": get_api_config(db, user.id, agent.model_name) is not None or _enterprise_model_ready(db, agent),
            "embedding_configured": embedding_configured,
            "rag_ready": agent.rag_enabled != 1 or (embedding_configured and len(done_docs) > 0),
            "skill_count": len(skill_config.get("skills", [])),
            "tool_count": len(skill_config.get("tool_names", [])),
            "knowledge_done_count": len(done_docs),
            "knowledge_total_count": len(docs),
        },
        "prompt": {
            "raw": prompt_data,
            "base_prompt": base_prompt,
            "profile_prompt": profile_prompt,
            "skill_prompt": skill_prompt,
            "final_prompt": final_prompt,
        },
        "skills": skill_config.get("skills", []),
        "tool_names": skill_config.get("tool_names", []),
        "tool_defaults_map": skill_config.get("tool_defaults_map", {}),
        "permissions": skill_config.get("permissions", {}),
        "resources": skill_config.get("resources", []),
        "knowledge": [
            {
                "id": doc.id,
                "file_name": doc.file_name,
                "status": doc.status,
                "chunk_count": doc.chunk_count,
                "error_msg": doc.error_msg,
            }
            for doc in docs
        ],
    }


def dry_run_agent(db, user, agent_id: int, user_message: str, conversation_id: int = None) -> Optional[Dict[str, Any]]:
    """模拟一次 Agent 装配流程，不调用 LLM，不写入消息。"""
    import service.conversation_service as conv_service
    from models.conversation_dao import get_conversation_by_id
    from service.rag import search_entry

    agent = get_agent_by_id(db, agent_id)
    if not agent or agent.user_id != user.id:
        return None
    message = (user_message or "").strip()
    if not message:
        raise ValueError("测试问题不能为空")

    debug = get_agent_debug(db, user, agent_id)
    history = []
    conversation = None
    if conversation_id is not None:
        conversation = get_conversation_by_id(db, conversation_id)
        if not conversation or conversation.user_id != user.id or conversation.agent_id != agent_id:
            raise ValueError("会话不存在或无权限")
        history = conv_service.load_history_for_llm(db, conversation_id, limit=20)

    rag = {
        "enabled": agent.rag_enabled == 1,
        "ok": True,
        "error": "",
        "hit_count": 0,
        "results": [],
        "citations": [],
        "mode": "off",
        "context_preview": "",
    }
    full_prompt = debug["prompt"]["final_prompt"]
    if agent.rag_enabled:
        try:
            res = search_entry.search_for_agent(
                user.id, agent_id, message,
                top_k=int(agent.kb_top_k or 5),
                rerank=bool(agent.kb_rerank_enabled),
                refuse_when_empty=bool(agent.kb_refuse_when_empty),
            )
            rag["results"] = res.get("hits", [])
            rag["hit_count"] = len(res.get("hits", []))
            rag["citations"] = res.get("citations", [])
            rag["mode"] = res.get("mode", "agent")
            rag_context = res.get("context", "")
            rag["context_preview"] = rag_context[:2000]
            if rag_context:
                full_prompt = (
                    f"{full_prompt}\n\n"
                    f"=== 知识库参考资料（按编号）===\n{rag_context}\n=== 参考资料结束 ===\n"
                    f"若参考资料不足以回答，请如实说明，不要编造。{UNTRUSTED_RULE}"
                )
        except Exception as e:
            rag["ok"] = False
            rag["error"] = str(e)

    messages = []
    if full_prompt:
        messages.append({"role": "system", "content": full_prompt})
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    return {
        "agent": debug["agent"],
        "readiness": debug["readiness"],
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
        } if conversation else None,
        "input": {
            "message": message,
            "conversation_id": conversation_id,
        },
        "rag": rag,
        "skills": debug["skills"],
        "tool_names": debug["tool_names"],
        "tool_defaults_map": debug["tool_defaults_map"],
        "permissions": debug.get("permissions", {}),
        "resources": debug.get("resources", []),
        "prompt": {
            "base_prompt": debug["prompt"]["base_prompt"],
            "profile_prompt": debug["prompt"].get("profile_prompt", ""),
            "skill_prompt": debug["prompt"]["skill_prompt"],
            "final_prompt": full_prompt,
        },
        "messages": messages,
        "stats": {
            "history_message_count": len(history),
            "final_prompt_chars": len(full_prompt),
            "message_count": len(messages),
            "tool_count": len(debug["tool_names"]),
        },
    }


def _enterprise_model_ready(db, agent) -> bool:
    """企业助手：助手创建人、企业所有者 / 管理员、平台管理员里有人配了这个模型（运行时会用企业的连接，
    规则同 llm_config_service.use_agent_credentials）。"""
    if getattr(agent, "agent_type", None) not in ("central", "department"):
        return False
    from sqlalchemy import select, text
    from models.init_db import Role, association_table
    from service.admin_service import ADMIN_ROLE_NAMES
    from service.llm.llm_config_service import get_api_config
    org = getattr(agent, "organization_id", None) or db.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1")).scalar()
    admins = [r[0] for r in db.execute(text(
        "SELECT om.user_id FROM organization_members om JOIN enterprise_role r ON r.id = om.role_id "
        "WHERE om.organization_id = :o AND om.status = 'active' AND r.scope = 'organization' AND r.code IN ('owner', 'admin')"),
        {"o": org}).all()] if org else []
    platform = db.execute(select(association_table.c.user_id).join(Role, Role.id == association_table.c.role_id)
                          .where(Role.role_name.in_(ADMIN_ROLE_NAMES))).scalars().all()
    return any(get_api_config(db, uid, agent.model_name) is not None for uid in [agent.user_id, *admins, *platform])
