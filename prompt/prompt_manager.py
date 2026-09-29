import os
import tempfile
import yaml
from pathlib import Path

from utils.logger_handler import get_logger
logger = get_logger("prompt_manager")
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_DIR.mkdir(exist_ok=True)


def _atomic_write_yaml(file_path: Path, data: dict) -> None:
    """临时文件 + os.replace 原子替换（第五轮审计 P1-7）。

    之前是直接 `open(file_path, 'w')`——这个调用本身就会先截断文件再写，
    写到一半崩了（进程被杀、磁盘满）文件就留在被截断/损坏的状态，没有任何
    办法恢复成写之前的内容。改成先写一份临时文件、写完整个成功了再
    `os.replace` 原子换上去：写失败的话原文件完全没被动过，`os.replace`
    这一步本身在同一个文件系统内是原子操作，不会出现"换了一半"的中间状态。
    """
    directory = file_path.parent
    fd, temp_path = tempfile.mkstemp(prefix=".prompt-", suffix=".yaml.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        os.replace(temp_path, file_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


#创建yml
def create_prompt_file(agent_id,role,task,constraints,output):
    """创建智能体的提示词 YAML 文件"""
    file_path = PROMPT_DIR / f"{agent_id}.yaml"
    data = {"role": role,
            "task": task,
            "constraints": constraints,
            "output": output}
    _atomic_write_yaml(file_path, data)
    logger.info(f"创建提示词文件成功: agent_id={agent_id}, path={file_path}")
    return str(file_path)
#读取system——prompt
def read_prompt_file(agent_id):
    # 构造文件路径
    file_path = PROMPT_DIR / f"{agent_id}.yaml"
    if not file_path.exists():
        logger.warning(f"提示词文件不存在: agent_id={agent_id}")
        return None
    with open(file_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    logger.info(f"读取提示词文件成功: agent_id={agent_id}")
    return data

#更新yml
def update_prompt_file(agent_id, role, task, constraints, output):
    file_path = PROMPT_DIR / f"{agent_id}.yaml"
    if not file_path.exists():
        logger.warning(f"更新失败，文件不存在: agent_id={agent_id}")
        return None
    data = {"role": role, "task": task, "constraints": constraints, "output": output}
    _atomic_write_yaml(file_path, data)
    logger.info(f"更新提示词文件成功: agent_id={agent_id}")
    return str(file_path)
#删除yml
def delete_prompt_file(agent_id):
    file_path = PROMPT_DIR / f"{agent_id}.yaml"
    if not file_path.exists():
        logger.warning(f"删除失败，文件不存在: agent_id={agent_id}")
        return False
    file_path.unlink()
    logger.info(f"删除提示词文件成功: agent_id={agent_id}")
    return True

def build_prompt(agent_id):
    """读取 YAML 配置，组装成完整的 system prompt 字符串"""
    data = read_prompt_file(agent_id)
    if data is None:
        logger.warning(f"构建提示词失败，文件不存在: agent_id={agent_id}")
        return None
    sections = []
    if data.get("role"):
        sections.append(f"角色: {data['role']}")
    if data.get("task"):
        sections.append(f"任务: {data['task']}")
    if data.get("constraints"):
        sections.append(f"约束: {data['constraints']}")
    if data.get("output"):
        sections.append(f"输出: {data['output']}")
    if not sections :
        return None
    system_prompt = "\n\n".join(sections)
    logger.info(f"构建提示词成功: agent_id={agent_id}")
    return system_prompt


