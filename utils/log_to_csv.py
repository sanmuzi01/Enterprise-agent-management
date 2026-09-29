import os
import csv
from utils.path_tool import get_abs_path
from utils.logger_handler import logger

# 路径配置
LOG_DIR = get_abs_path("logs")
CSV_PATH = get_abs_path("data/external/records.csv")

# 核心函数：日志转CSV
def parse_log_to_csv():
    """
    将日志中的 USER_BEHAVIOR 解析为 CSV
    """

    if not os.path.exists(LOG_DIR):
        logger.warning(f"日志目录不存在: {LOG_DIR}")
        return

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)

    data = []

    # 读取所有日志文件
    for file in os.listdir(LOG_DIR):
        if not file.endswith(".log"):
            continue

        log_path = os.path.join(LOG_DIR, file)

        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    # 只处理行为日志
                    if "USER_BEHAVIOR" not in line:
                        continue

                    try:
                        # 示例日志：
                        # 2026-06-16 - agent - INFO - USER_BEHAVIOR|1001|rag_query|success|120|2026-06
                        content = line.split(" - ")[-1].strip()
                        if not content.startswith("USER_BEHAVIOR"):
                            continue
                        parts = content.split("|")

                        if len(parts) != 6:
                            continue
                        _, user_id, action, status, duration, time = parts
                        data.append([
                            user_id,
                            action,
                            status,
                            duration,
                            time
                        ])
                    except Exception as e:
                        logger.debug(f"解析日志行失败: {line} | {e}")
                        continue

        except Exception as e:
            logger.error(f"读取日志文件失败: {log_path} | {e}")

    # 写入CSV（覆盖写，避免重复）
    try:
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # 表头
            writer.writerow([
                "user_id",
                "action",
                "status",
                "duration",
                "time"
            ])
            writer.writerows(data)
        logger.info(f"CSV更新完成，共写入 {len(data)} 条数据 → {CSV_PATH}")

    except Exception as e:
        logger.error(f"写入CSV失败: {e}")

# 增强功能：只追加新数据（进阶）
def parse_log_to_csv_incremental():
    """
    增量更新（不会重复写）
    适合生产环境
    """
    if not os.path.exists(LOG_DIR):
        logger.warning("日志目录不存在")
        return

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)

    existing = set()

    # 读取已有CSV
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            for line in f.readlines()[1:]:
                existing.add(line.strip())

    new_data = []

    for file in os.listdir(LOG_DIR):
        if not file.endswith(".log"):
            continue

        log_path = os.path.join(LOG_DIR, file)

        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                if "USER_BEHAVIOR" not in line:
                    continue
                try:
                    content = line.split(" - ")[-1].strip()
                    parts = content.split("|")
                    if len(parts) != 6:
                        continue

                    _, user_id, action, status, duration, time = parts
                    row = f"{user_id},{action},{status},{duration},{time}"
                    if row not in existing:
                        new_data.append([
                            user_id, action, status, duration, time
                        ])

                except Exception:
                    continue

    # 追加写
    if new_data:
        write_header = not os.path.exists(CSV_PATH)

        with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            if write_header:
                writer.writerow(["user_id", "action", "status", "duration", "time"])

            writer.writerows(new_data)

        logger.info(f"增量更新CSV，新增 {len(new_data)} 条数据")
    else:
        logger.info("没有新增日志数据")

