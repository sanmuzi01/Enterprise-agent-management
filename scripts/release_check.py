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

# 部门工作台 AI 工作成果（材料整理 → 人工核对 → 业务草稿）
AUTOMATION_FILES = [
    "FasdtApi/automation_work.py",
    "service/automation_spec.py",
    "service/automation_work_service.py",
    "service/workflows/__init__.py",
    "service/workflows/base.py",
    "frontend/src/components/WorkflowForm.vue",
    "frontend/src/components/WorkflowField.vue",
    "migrations/versions/20261001_0002_automation_work.py",
    "frontend/src/api/automationWork.ts",
    "frontend/src/components/AutomationWorkPanel.vue",
    "scripts/check_automation_browser.py",
    "scripts/e2e_automation_workflows.py",
    "docs/agent-productivity-workflows.md",
    "enterprise-business-hub/src/main/java/com/enterprisehub/web/ApiExceptionHandler.java",
    # 待办中心与主动提醒
    "FasdtApi/work_center.py",
    "service/work_item_service.py",
    "service/notification_center.py",
    "service/reminders/__init__.py",
    "service/reminders/rules.py",
    "service/automation_metrics.py",
    "service/handoff_service.py",
    "service/department_access.py",
    "migrations/versions/20261003_0002_agent_handoff.py",
    "migrations/versions/20261003_0001_automation_metrics.py",
    "frontend/src/views/admin/AdminAutomation.vue",
    "migrations/versions/20261002_0003_work_items_notifications.py",
    "frontend/src/views/TodoCenter.vue",
    "frontend/src/components/NotificationBell.vue",
    # 财务自动记账
    "FasdtApi/finance_vouchers.py",
    "service/finance_voucher_service.py",
    "service/tools/finance_voucher.py",
    "frontend/src/api/financeVouchers.ts",
    "frontend/src/components/FinanceVoucherModule.vue",
    "scripts/e2e_finance_vouchers.py",
    "enterprise-business-hub/src/main/resources/db/migration/V6__finance_vouchers.sql",
    "enterprise-business-hub/src/main/java/com/enterprisehub/finance/VoucherService.java",
    # IT 服务台
    "FasdtApi/it_service.py",
    "service/it_service.py",
    "service/hub_gateway.py",
    "service/workflows/ticket.py",
    "service/tools/it_service.py",
    "frontend/src/api/itService.ts",
    "frontend/src/components/TicketModule.vue",
    "frontend/src/components/ItDeskModule.vue",
    "scripts/e2e_it_service.py",
    "enterprise-business-hub/src/main/resources/db/migration/V7__it_service_desk.sql",
    "enterprise-business-hub/src/main/java/com/enterprisehub/it/ItTicketService.java",
    # 人事入转调离
    "FasdtApi/hr_cases.py",
    "service/hr_service.py",
    "service/tools/hr_cases.py",
    "frontend/src/api/hrCases.ts",
    "frontend/src/components/HrCaseModule.vue",
    "scripts/e2e_hr_cases.py",
    "enterprise-business-hub/src/main/resources/db/migration/V8__hr_lifecycle.sql",
    "enterprise-business-hub/src/main/java/com/enterprisehub/hr/HrCaseService.java",
    "service/department_home.py",
    "service/orchestration_service.py",
    "FasdtApi/orchestration.py",
    "frontend/src/components/OrchestrationPanel.vue",
    "migrations/versions/20261006_0001_orchestration.py",
    # 可观测性与问题中心
    "service/observability/context.py",
    "service/observability/redact.py",
    "service/observability/error_codes.py",
    "service/observability/issues.py",
    "service/observability/sentry_setup.py",
    "FasdtApi/issues.py",
    "frontend/src/views/admin/AdminIssues.vue",
    "migrations/versions/20261007_0002_system_issue.py",
    "scripts/drill_java_down.py",
    # 可靠事件
    "service/events/outbox.py",
    "service/events/runner.py",
    "service/events/handlers.py",
    "migrations/versions/20261007_0003_outbox.py",
    "scripts/drill_batch_restart.py",
    # 文件导入
    "service/document_intake.py",
    "service/automation_batch_service.py",
    "frontend/src/components/AutomationBatchPanel.vue",
    "migrations/versions/20261007_0001_automation_batch.py",
    "scripts/e2e_batch_browser.py",
    "tests/test_document_intake.py",
    # 部门责任执行
    "FasdtApi/responsibility.py",
    "service/responsibility_service.py",
    "service/tools/responsibility.py",
    "service/workflows/responsibility.py",
    "service/workflows/date_text.py",
    "service/reminders/responsibility_rules.py",
    "frontend/src/api/responsibility.ts",
    "frontend/src/components/ResponsibilityModule.vue",
    "frontend/src/components/ResponsibilityPlanPanel.vue",
    "frontend/src/components/ResponsibilityTaskPanel.vue",
    "scripts/e2e_responsibility.py",
    "scripts/e2e_responsibility_browser.py",
    "enterprise-business-hub/src/main/resources/db/migration/V9__responsibility.sql",
    "enterprise-business-hub/src/main/java/com/enterprisehub/responsibility/ResponsibilityService.java",
    "docs/demo-backup/responsibility/index.html",
    # 演示包装
    "scripts/demo.py",
    "scripts/seed_enterprise_demo.py",
    "scripts/demo_walkthrough.py",
    "service/llm/offline_demo.py",
    "service/readiness.py",
    "docs/demo-script.md",
    "docs/demo-backup/index.html",
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
    required = REQUIRED_FILES + WIDGET_FILES + AUTOMATION_FILES
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
    if "_run_reminder_tick" not in worker or "run_due_rules" not in worker:
        raise SystemExit("service/background_worker.py 未接入业务提醒规则")
    print("OK 业务提醒规则已接入 background_worker")


def check_automation_wired() -> None:
    """工作成果接口必须挂到主应用；注册的每个工作流都要有完整声明（前端表单由声明驱动）。"""
    print("\n检查 AI 工作成果接口已接入...")
    main_py = (ROOT / "FasdtApi/main.py").read_text(encoding="utf-8")
    if "app.include_router(automation_work_router)" not in main_py:
        raise SystemExit("FasdtApi/main.py 未挂载 /enterprise/automation 路由")
    if "app.include_router(finance_vouchers_router)" not in main_py:
        raise SystemExit("FasdtApi/main.py 未挂载 /enterprise/finance/vouchers 路由")
    if "app.include_router(hr_cases_router)" not in main_py:
        raise SystemExit("FasdtApi/main.py 未挂载 /enterprise/hr 路由")
    if "app.include_router(responsibility_router)" not in main_py:
        raise SystemExit("FasdtApi/main.py 未挂载 /enterprise/responsibility 路由")
    if "app.include_router(it_service_router)" not in main_py:
        raise SystemExit("FasdtApi/main.py 未挂载 /enterprise/it 路由")
    if "getWorkflows" not in (ROOT / "frontend/src/api/automationWork.ts").read_text(encoding="utf-8"):
        raise SystemExit("前端未从工作流目录接口加载工作类型")
    sys.path.insert(0, str(ROOT))
    from service.workflows import all_workflows
    incomplete = [w.id for w in all_workflows()
                  if not (w.form and w.instructions and w.source_label and callable(w.write) and callable(w.evidence))]
    if incomplete:
        raise SystemExit("工作流声明不完整: " + ", ".join(incomplete))
    if "responsibility" not in {w.id for w in all_workflows()}:
        raise SystemExit("责任计划整理工作流没有注册")
    print(f"OK 已挂载，已注册工作流 {[w.id for w in all_workflows()]}")


def main() -> None:
    python = sys.executable
    check_required_files()
    parse_configs()
    check_no_container_hostnames()
    check_widget_scheduler_wired()
    check_automation_wired()
    run([python, "-m", "compileall", "FasdtApi", "service", "models", "utils", "scripts", "tests"])
    run([python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])
    run(["npm.cmd" if sys.platform.startswith("win") else "npm", "run", "frontend:build"])
    print("\n发布自检完成")


if __name__ == "__main__":
    main()
