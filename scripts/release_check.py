import json
import pathlib
import subprocess
import sys
from typing import List

import yaml


ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(command: List[str]) -> None:
    print(f"\n$ {' '.join(command)}")
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


# 上线关键文件（非容器部署）
REQUIRED_FILES = [
    ".env.production.example",
    "scripts/load_test.py",
    "scripts/crawl_check.py",
    "alembic.ini",
    "migrations/env.py",
    "migrations/script.py.mako",
    # 迁移历史在 2026-09-24 重新定过基线（旧的 0001~0008 挪到
    # migrations/archive_pre_baseline/，只留历史记录，不再参与 alembic upgrade，
    # 见 docs/db-migration-plan.md）——这里只检查当前生效的基线文件，不追踪
    # 每一条迁移，那是 tests/test_db_migrations.py 的静态检查在做的事。
    "migrations/versions/20260924_0001_trusted_baseline.py",
    "docs/deployment.md",
    "docs/release-checklist.md",
    "docs/load-testing.md",
    "docs/database-migrations.md",
    "docs/testing.md",
    # 知识库空间（阶段1）
    "FasdtApi/knowledge_space.py",
    "service/knowledge_space/space_async_service.py",
    "service/knowledge_space/space_service.py",
    "service/knowledge_space/binding_service.py",
    "service/knowledge_space/membership.py",
    "service/knowledge_space/document_service.py",
    "models/knowledge_space_dao.py",
    "models/knowledge_space_async_dao.py",
    "models/agent_knowledge_space_dao.py",
    "scripts/migrate_agent_kb_to_space.py",
    "docs/knowledge-space-plan.md",
    # 知识库空间（阶段3）：多空间联合检索 + 引用 + Agent 绑定
    "service/rag/space_search.py",
    "frontend/src/components/knowledge/CitationList.vue",
    # 知识库空间（阶段4）：知识库调试台
    "FasdtApi/rag_debug.py",
    "service/rag/debug_service.py",
    "models/rag_debug_dao.py",
    "frontend/src/api/ragDebug.ts",
    "frontend/src/views/knowledge/RagDebugConsole.vue",
    "frontend/src/components/knowledge/RagTracePanel.vue",
    # 知识库空间（阶段5）：健康分 + 按空间评估 + Widget 健康 connector
    "service/knowledge_space/health_service.py",
    "service/widgets/connectors/knowledge_space.py",
    "frontend/src/views/knowledge/SpaceHealth.vue",
    # 知识库空间（阶段6）：企业权限 —— 成员/角色 + 审计 + 管理员视角
    "models/space_member_dao.py",
    "models/kb_audit_dao.py",
    "frontend/src/components/knowledge/SpaceMembersPanel.vue",
    "frontend/src/views/admin/AdminKnowledgeSpaces.vue",
    # 前端信息架构整合：助手空间标签导航 + 分区标签
    "frontend/src/components/agent/AgentSubnav.vue",
    "frontend/src/components/SectionTabs.vue",
    "frontend/src/views/AgentKnowledgePanel.vue",
    # 反向代理 + 监控模板
    "deploy/nginx.conf",
    "deploy/prometheus.yml",
    "deploy/prometheus-rules.yml",
    "deploy/grafana/provisioning/datasources/prometheus.yml",
    "deploy/grafana/provisioning/dashboards/dashboards.yml",
    "deploy/grafana/provisioning/dashboards/json/agent-platform-overview.json",
]

# 自定义工作台组件平台（P1 + P2）：核心模块必须在位
WIDGET_FILES = [
    "FasdtApi/user_widget.py",
    "service/widget_async_service.py",
    "service/widgets/schema.py",
    "service/widgets/runner.py",
    "service/widgets/scheduler.py",
    "service/widgets/retention.py",
    "service/widgets/shape.py",
    "service/widgets/validator.py",
    "service/widgets/designer.py",
    "service/widgets/processors.py",
    "service/widgets/connectors/__init__.py",
    "service/widgets/connectors/http_api.py",
    "service/widgets/connectors/web_page.py",
    "service/widgets/connectors/knowledge_base.py",
    "service/rag/search_entry.py",
    "models/user_widget_async_dao.py",
    "frontend/src/views/WidgetStudio.vue",
    "frontend/src/components/widgets/registry.ts",
    "docs/widget-platform.md",
]

YAML_CONFIGS = [
    "deploy/prometheus.yml",
    "deploy/prometheus-rules.yml",
    "deploy/grafana/provisioning/datasources/prometheus.yml",
    "deploy/grafana/provisioning/dashboards/dashboards.yml",
]
JSON_CONFIGS = [
    "deploy/grafana/provisioning/dashboards/json/agent-platform-overview.json",
]


def check_required_files() -> None:
    print("\n检查上线关键文件...")
    required = REQUIRED_FILES + WIDGET_FILES
    missing = [name for name in required if not (ROOT / name).exists()]
    if missing:
        raise SystemExit("缺少上线关键文件: " + ", ".join(missing))
    for name in required:
        print(f"OK {name}")


def parse_configs() -> None:
    print("\n检查监控配置可解析...")
    for name in YAML_CONFIGS:
        yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
        print(f"OK {name}")
    for name in JSON_CONFIGS:
        json.loads((ROOT / name).read_text(encoding="utf-8"))
        print(f"OK {name}")


def check_no_container_hostnames() -> None:
    """非容器部署：deploy/ 配置不应再指向 compose 服务名。"""
    print("\n检查 deploy/ 无残留容器主机名...")
    offenders = []
    for name in ("deploy/nginx.conf", "deploy/prometheus.yml",
                 "deploy/grafana/provisioning/datasources/prometheus.yml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for token in ("http://api:", " api:8000", "http://prometheus:", "http://grafana:"):
            if token in text:
                offenders.append(f"{name} 含 '{token.strip()}'")
    if offenders:
        raise SystemExit("deploy/ 仍指向容器服务名: " + "; ".join(offenders))
    print("OK deploy/ 主机名已本地化")


def check_widget_scheduler_wired() -> None:
    """组件调度必须挂在后台 Worker 上。"""
    print("\n检查组件定时调度已接入 Worker...")
    worker = (ROOT / "service/background_worker.py").read_text(encoding="utf-8")
    if "_run_widget_scheduler_tick" not in worker or "run_due_widgets" not in worker:
        raise SystemExit("service/background_worker.py 未接入组件定时调度")
    print("OK 组件调度已接入 background_worker")


def main() -> None:
    python = sys.executable
    check_required_files()
    parse_configs()
    check_no_container_hostnames()
    check_widget_scheduler_wired()
    run([python, "-m", "compileall", "FasdtApi", "service", "models", "utils", "scripts", "tests"])
    run([python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])
    run(["npm.cmd" if sys.platform.startswith("win") else "npm", "run", "frontend:build"])
    print("\n发布自检完成")


if __name__ == "__main__":
    main()
