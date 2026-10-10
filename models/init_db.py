from utils.timeutil import utcnow
from typing import List
from typing import Generator
from utils.db_probe import connect_args
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, Table, Index, Float, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.engine import URL
from sqlalchemy.orm import declarative_base, sessionmaker, Mapped, relationship
from dotenv import load_dotenv
import os


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量，格式错误时使用默认值，避免配置错误导致应用直接崩溃。"""

    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = True) -> bool:
    """读取布尔环境变量，支持 1/true/yes/on 和 0/false/no/off。"""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# 数据库连接
load_dotenv()
DB_USER=os.getenv("DB_USER")
DB_PASSWORD=os.getenv("DB_PASSWORD")
DB_HOST=os.getenv("DB_HOST")
DB_PORT=os.getenv("DB_PORT")
DB_NAME=os.getenv("DB_NAME")
missing_db_config = [
    name for name, value in {
        "DB_USER": DB_USER,
        "DB_PASSWORD": DB_PASSWORD,
        "DB_HOST": DB_HOST,
        "DB_PORT": DB_PORT,
        "DB_NAME": DB_NAME,
    }.items()
    if not value
]
if missing_db_config:
    raise RuntimeError(f"缺少数据库环境变量: {', '.join(missing_db_config)}")
# 第五轮审计 P1-6：不能用 f-string 直接拼——密码里如果有 @ : / % 之类的字符，
# 会被误解析成主机名/路径的一部分。URL.create() 负责正确的百分号编码，
# render_as_string(hide_password=False) 拿到编码后的完整连接串（这里需要真密码
# 去连库，不是给人看的日志，所以不能用默认的 hide_password=True）。
DATABASE_URL = URL.create(
    "mysql+pymysql", username=DB_USER, password=DB_PASSWORD,
    host=DB_HOST, port=int(DB_PORT), database=DB_NAME,
).render_as_string(hide_password=False)
DB_POOL_SIZE = _env_int("DB_POOL_SIZE", 10)
DB_MAX_OVERFLOW = _env_int("DB_MAX_OVERFLOW", 20)
DB_POOL_TIMEOUT = _env_int("DB_POOL_TIMEOUT", 30)
DB_POOL_RECYCLE = _env_int("DB_POOL_RECYCLE", 1800)
DB_POOL_PRE_PING = _env_bool("DB_POOL_PRE_PING", True)

# 数据库连接池：
# pool_pre_ping 会在取连接前探测连接是否还活着，避免 MySQL 空闲断开后请求直接报错。
# pool_recycle 主动回收旧连接，应小于 MySQL wait_timeout，适合长时间运行的生产服务。
engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT,
    pool_recycle=DB_POOL_RECYCLE,
    pool_pre_ping=DB_POOL_PRE_PING,
    pool_use_lifo=True,
    connect_args=connect_args(),      # 含 connect_timeout：数据库“半通不通”时连接线程不会无限卡住（见 utils/db_probe.py）
)

# ORM基类
Base = declarative_base()
# 用户角色关联表
association_table = Table(
    "user_role",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("user.id"),primary_key=True),
    Column("role_id", Integer, ForeignKey("role.id"),primary_key=True)
)
# 用户表
class User(Base):
    __tablename__ = "user"
    id = Column(Integer,primary_key=True,autoincrement=True)
    name = Column(String(255),nullable=False,unique=True)
    phone = Column(String(20),nullable=True,unique=True)
    password = Column(String(255),nullable=False)
    age = Column(Integer)
    is_disabled = Column(Integer, default=0)
    last_login_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    # token 版本号：改密码/管理员重置密码/管理员强制下线/用户"退出所有设备"都会 +1；
    # JWT 里带着签发时的版本号，鉴权时两边一对，不相等就是旧 token，直接拒绝（见 service/dependencies.py）。
    auth_version = Column(Integer, nullable=False, default=0, server_default="0")
    password_changed_at = Column(DateTime, nullable=True)
    selected_agent_id = Column(Integer,ForeignKey("agent.id", name="fk_user_selected_agent"),nullable=True)    #关联关系
    roles:Mapped[List["Role"]]=relationship(secondary=association_table,lazy=False,back_populates="users")
    #1对1的关系
    #role = relationship("Role",lazy=False,back_populates="user")


class UserProfile(Base):
    """用户画像：保存用户希望 AI 长期遵循的身份、偏好和沟通方式。"""
    __tablename__ = "user_profile"
    __table_args__ = (
        Index("idx_user_profile_user_id", "user_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_user_profile_user"), nullable=False, unique=True)
    occupation = Column(String(100), nullable=True)              # 职业/身份
    skills = Column(Text, nullable=True)                         # 技能背景
    preferences = Column(Text, nullable=True)                    # 长期偏好
    communication_style = Column(String(50), default="balanced") # 回答风格
    persona = Column(String(50), default="professional")         # 助手人格
    extra_info = Column(Text, nullable=True)                     # 其他补充信息
    auto_summary = Column(Text, nullable=True)                   # AI 自动提炼的用户画像
    last_inferred_at = Column(DateTime, nullable=True)           # 最近一次自动画像更新时间
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class UserWorkspace(Base):
    """用户工作台配置：保存每个用户自己的模块、小窗口和布局。"""
    __tablename__ = "user_workspace"
    __table_args__ = (
        Index("idx_user_workspace_user_id", "user_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_user_workspace_user"), nullable=False, unique=True)
    modules_json = Column(Text, nullable=False)
    widgets_json = Column(Text, nullable=False)
    layout_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# 智能体表
class Agent(Base):
    __tablename__ = "agent"
    __table_args__ = (
        Index("idx_agent_user_id_id", "user_id", "id"),
        Index("idx_agent_org", "organization_id"),
        Index("idx_agent_team", "team_id"),
        UniqueConstraint("department_publish_key", name="uq_agent_department_publish"),
    )
    id = Column(Integer,primary_key=True,autoincrement=True)
    user_id = Column(Integer,ForeignKey("user.id", name="fk_agent_user"),nullable=False)
    name = Column(String(255),nullable=False)
    # Prompt文件路径
    prompt_file = Column( String(255),nullable=True)
    model_name = Column(String(100), default="glm-4")             # 用的大模型
    rag_enabled = Column(Integer, default=0)                       # 是否启用RAG（0=否，1=是），总开关
    memory_enabled = Column(Integer, default=1)                    # 是否启用长期记忆（0=否，1=是）
    temperature = Column(Integer, default=70)                      # 温度参数（0-100，控制创造性）
    # ---- 知识库空间检索行为（阶段1 加列；阶段3 接线）----
    kb_top_k = Column(Integer, nullable=False, default=5)
    kb_rerank_enabled = Column(Integer, nullable=False, default=0)
    kb_force_citation = Column(Integer, nullable=False, default=1)     # 回答强制带来源
    kb_refuse_when_empty = Column(Integer, nullable=False, default=1)  # 无命中时拒答
    # ---- Phase 3D 阶段1：资源归属与密级（docs/enterprise-rbac-plan.md 9.5）----
    # 由后端按当前用户/目标 team 计算写入，不接受前端传值。
    organization_id = Column(Integer, ForeignKey("organizations.id", name="fk_agent_org"), nullable=True)
    team_id = Column(Integer, ForeignKey("teams.id", name="fk_agent_team"), nullable=True)
    scope_type = Column(String(20), nullable=False, default="personal")   # personal/department/enterprise
    sensitivity = Column(String(20), nullable=False, default="internal")  # public/internal/confidential/restricted
    # ---- Phase 3D 阶段5：乐观锁 + 发布生命周期字段（docs/enterprise-rbac-plan.md 9.5）----
    # 只加字段，还没接入比对/强制逻辑：没有任何路由会把 lifecycle_status 改成 draft
    # 以外的值，也没有任何更新路径会去比对 row_version，现存行为完全不变；真正的
    # compare-and-swap 和"已发布不能原地改"等到真有发布入口时再接，跟阶段1的加字段
    # 节奏一样。
    row_version = Column(Integer, nullable=False, default=0)
    lifecycle_status = Column(String(20), nullable=False, default="draft")  # draft/reviewing/published/retired
    # ---- Phase 3D 阶段3：中央 Agent 受控路由（docs/enterprise-rbac-plan.md 9.5）----
    # agent_type：central（中央助手，聊天时先跑规则路由）/ department（部门助手，路由目标）/
    # personal（普通助手，默认值，不参与路由，现存 Agent 全部是这个值）。
    # department_code：agent_type=department 时标记它服务哪个部门（hr/procurement/sales/
    # finance/it），central/personal 通常为 NULL。校验（合法枚举值）在 service 层，
    # 不在这里加 CHECK——跟 EnterpriseRole 的 role_id 校验放在 service 层是同一个理由。
    agent_type = Column(String(20), nullable=False, default="personal")
    department_code = Column(String(20), nullable=True)
    # 同一个部门（team）同时只能有一个 published 的部门 Agent。由
    # service/agent_admin_service.py 维护：published 时写成 "t{team_id}"，其余状态清成 NULL；
    # MySQL 唯一索引允许多个 NULL，只对已发布的那一个做唯一约束。不同部门可以各自发布
    # 同一业务方向的 Agent（销售一部、二部各有 CRM Agent），见迁移 20261002_0001。
    department_publish_key = Column(String(20), nullable=True)
    # 运行方式：builtin = 平台自带的运行循环（提示词 + 工具 + 知识库）；
    # external = 把对话转发给企业自己部署的 Agent 服务（地址等配置在 agent_external_endpoint）。
    runtime_type = Column(String(20), nullable=False, default="builtin", server_default="builtin")
    # 企业智能体的“档案”：它能做什么（给管理员和使用者看）、由谁维护（出了问题找谁）。
    description = Column(String(500), nullable=True)
    maintainer = Column(String(100), nullable=True)
    skills: Mapped[List["Skill"]] = relationship(
        secondary="agent_skill", lazy=False, back_populates="agents"
    )
# 角色表
class Role(Base):
    __tablename__ = "role"
    id = Column(Integer,primary_key=True,autoincrement=True)
    role_name = Column(String(255),nullable=False,unique=True)
    description = Column(String(255))
    #关联关系
    users:Mapped[List["User"]]=relationship(secondary=association_table,lazy=False,back_populates="roles")
    #1对1的关系
    #user = relationship("User",lazy=False,back_populates="role")
#llm的apikey
class AutomationWork(Base):
    """Persisted AI work products. Business writes remain in the Java service."""
    __tablename__ = "automation_work"
    __table_args__ = (
        UniqueConstraint("user_id", "request_key", name="uq_automation_request"),
        Index("idx_automation_owner_team", "user_id", "team_id", "created_at"),
        Index("idx_automation_batch", "batch_id"),
    )
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    request_key = Column(String(36), nullable=False)
    kind = Column(String(30), nullable=False)
    model_name = Column(String(100), nullable=False)
    sensitivity = Column(String(20), nullable=False, default="internal")
    source_text = Column(Text, nullable=False)
    customer_id = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="processing")
    proposal_json = Column(Text, nullable=True)
    accepted_json = Column(Text, nullable=True)
    business_result_json = Column(Text, nullable=True)
    business_checks_json = Column(Text, nullable=True)
    completed_tasks_json = Column(Text, nullable=False, default="[]")
    error_message = Column(String(300), nullable=True)
    elapsed_ms = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=True)
    edited = Column(Integer, nullable=False, default=0)
    applied_at = Column(DateTime, nullable=True)
    apply_attempts = Column(Integer, nullable=False, default=0)
    batch_id = Column(String(36), nullable=True)       # 批量整理：同一批次的材料共用，None = 单份整理
    batch_index = Column(Integer, nullable=True)                   # 在批次里的顺序（从 0 开始）
    batch_name = Column(String(255), nullable=True)                # 批次里这份材料的名称（通常是文件名）
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class AgentHandoff(Base):
    """中央 Agent 的转交记录：转给了谁、为什么（含"没转出去"的原因）、输入摘要、备选部门。"""
    __tablename__ = "agent_handoff"
    __table_args__ = (
        Index("idx_agent_handoff_created", "created_at"),
        Index("idx_agent_handoff_user", "user_id", "created_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_agent_handoff_user"), nullable=False)
    central_agent_id = Column(Integer, nullable=True)
    target_agent_id = Column(Integer, nullable=True)
    reason = Column(String(24), nullable=False)       # routed / no_match / no_usable_agent
    department_code = Column(String(20), nullable=True)
    detail_json = Column(Text, nullable=True)
    message_excerpt = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class SystemIssue(Base):
    """问题中心：同一种故障（按 fingerprint 聚合）只有一条，发生多少次累计多少次。不放用户、请求内容等会让同一问题被拆散的信息。"""
    __tablename__ = "system_issue"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_system_issue_fingerprint"),
        Index("idx_system_issue_status", "status", "last_seen_at"),
        Index("idx_system_issue_dept", "department_id", "status"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_no = Column(String(40), nullable=True)
    fingerprint = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    severity = Column(String(10), nullable=False, default="medium")      # low / medium / high / critical
    category = Column(String(20), nullable=False, default="code")        # code / dependency / security / data / task
    status = Column(String(20), nullable=False, default="OPEN")          # OPEN / ACKNOWLEDGED / INVESTIGATING / MITIGATED / RESOLVED / REGRESSED
    service = Column(String(40), nullable=False)
    operation = Column(String(200), nullable=True)
    error_code = Column(String(60), nullable=False)
    department_id = Column(Integer, nullable=True)
    responsible_user_id = Column(Integer, nullable=True)
    last_trace_id = Column(String(64), nullable=True)
    sentry_event_id = Column(String(64), nullable=True)
    affected_resource_type = Column(String(40), nullable=True)
    affected_resource_id = Column(String(64), nullable=True)
    occurrence_count = Column(Integer, nullable=False, default=1)
    retryable = Column(Integer, nullable=False, default=0)
    first_seen_at = Column(DateTime, nullable=False, default=utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=utcnow)
    acknowledged_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    root_cause = Column(Text, nullable=True)
    resolution = Column(Text, nullable=True)
    fix_version = Column(String(80), nullable=True)
    resolved_by = Column(Integer, nullable=True)
    verified_by = Column(Integer, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    regress_count = Column(Integer, nullable=False, default=0)


class IssueOccurrence(Base):
    """某个问题的单次发生（只保留最近一批，用来看 trace_id 和上下文）。detail 已脱敏。"""
    __tablename__ = "issue_occurrence"
    __table_args__ = (Index("idx_issue_occ_issue", "issue_id", "id"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, nullable=False)
    trace_id = Column(String(64), nullable=True)
    release = Column(String(80), nullable=True)
    http_status = Column(Integer, nullable=True)
    message = Column(String(500), nullable=True)
    detail_json = Column(Text, nullable=True)
    occurred_at = Column(DateTime, nullable=False, default=utcnow)


class IssueEvent(Base):
    """问题的处理记录：谁、何时、做了什么（确认、指派、备注、解决、验收、回归）。只追加。"""
    __tablename__ = "issue_event"
    __table_args__ = (Index("idx_issue_event_issue", "issue_id", "id"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, nullable=False)
    actor_user_id = Column(Integer, nullable=True)      # 空 = 系统（自动回归、依赖恢复）
    action = Column(String(30), nullable=False)
    note = Column(String(1000), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class AttendanceRule(Base):
    """考勤规则：企业默认（team_id 为空）或某个部门自己的上下班时间与迟到宽限。"""
    __tablename__ = "attendance_rule"
    __table_args__ = (UniqueConstraint("organization_id", "team_key", name="uq_attendance_rule"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False)
    team_id = Column(Integer, nullable=True)
    team_key = Column(Integer, nullable=False, default=0)       # team_id 或 0（企业默认），用来做唯一约束
    work_start = Column(String(5), nullable=False, default="09:00")
    work_end = Column(String(5), nullable=False, default="18:00")
    grace_minutes = Column(Integer, nullable=False, default=5)
    flex_minutes = Column(Integer, nullable=False, default=0, server_default="0")   # 弹性上班：上班时间之后多少分钟内到岗都不算迟到，晚到多少晚走多少
    updated_by = Column(Integer, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class AttendanceCalendar(Base):
    """工作日历：法定节假日和调休上班日每年都不一样，由人事按国务院公布的安排录入；没有记录的日期按周一到周五上班、周末休息。"""
    __tablename__ = "attendance_calendar"
    __table_args__ = (UniqueConstraint("organization_id", "day", name="uq_attendance_calendar"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False)
    day = Column(DateTime, nullable=False)
    kind = Column(String(10), nullable=False)       # workday（含调休上班）/ rest（休息日）/ holiday（法定节假日）
    note = Column(String(60), nullable=True)


class AttendanceAlias(Base):
    """打卡机/考勤系统里的名字（姓名、工号）与平台账号的对应：现实里两边的叫法经常不一样，人事确认一次就记住。"""
    __tablename__ = "attendance_alias"
    __table_args__ = (UniqueConstraint("organization_id", "alias", name="uq_attendance_alias"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False)
    alias = Column(String(80), nullable=False)
    user_id = Column(Integer, nullable=False)
    created_by = Column(Integer, nullable=True)


class AttendanceImport(Base):
    """一次考勤文件导入（打卡机/钉钉/企业微信导出的 Excel 或 CSV）：只记录文件名、格式、区间、条数，不保存文件本身。"""
    __tablename__ = "attendance_import"
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False)
    uploaded_by = Column(Integer, nullable=False)
    file_name = Column(String(255), nullable=False)
    source_format = Column(String(20), nullable=False)       # punch_rows 逐条打卡 / daily_summary 每日汇总
    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    punch_count = Column(Integer, nullable=False, default=0)
    new_punch_count = Column(Integer, nullable=False, default=0)
    unmatched_json = Column(Text, nullable=True)
    skipped_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class AttendancePunch(Base):
    __tablename__ = "attendance_punch"
    __table_args__ = (UniqueConstraint("user_id", "punch_at", name="uq_attendance_punch"), Index("idx_attendance_punch_org_day", "organization_id", "punch_at"))
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    punch_at = Column(DateTime, nullable=False)             # 北京时间的本地时间（打卡机导出的就是这个）
    import_id = Column(Integer, nullable=True)


class AttendanceAnomaly(Base):
    """考勤异常：由规则判断（不涉及模型），员工说明，人事/部门负责人认定。同一个人同一天同一类型只有一条。"""
    __tablename__ = "attendance_anomaly"
    __table_args__ = (UniqueConstraint("user_id", "work_date", "type", name="uq_attendance_anomaly"),
                      Index("idx_attendance_anomaly_team", "team_id", "status", "work_date"))
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False)
    team_id = Column(Integer, nullable=True)
    user_id = Column(Integer, nullable=False)
    work_date = Column(DateTime, nullable=False)
    type = Column(String(20), nullable=False)               # late / early_leave / missing_in / missing_out / absent / rest_day_work / overlong / leave_conflict
    severity = Column(String(8), nullable=False, default="medium")
    detail_json = Column(Text, nullable=False)
    status = Column(String(12), nullable=False, default="open")   # open / explained / confirmed / dismissed / cleared
    explanation = Column(String(500), nullable=True)
    explained_at = Column(DateTime, nullable=True)
    decided_by = Column(Integer, nullable=True)
    decision_note = Column(String(500), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class OutboxEvent(Base):
    """事务性发件箱：业务数据和事件在同一个数据库事务里写入，之后由发布器可靠地送出（至少一次）。payload 已脱敏，不含正文。"""
    __tablename__ = "outbox_event"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_outbox_event_id"),
        Index("idx_outbox_publish", "published_at", "next_publish_at"),
        Index("idx_outbox_topic", "topic", "id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), nullable=False)
    topic = Column(String(80), nullable=False)
    event_key = Column(String(64), nullable=True)
    event_type = Column(String(60), nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    aggregate_type = Column(String(40), nullable=False)
    aggregate_id = Column(String(64), nullable=False)
    organization_id = Column(Integer, nullable=True)
    department_id = Column(Integer, nullable=True)
    trace_id = Column(String(64), nullable=True)
    producer = Column(String(40), nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    published_at = Column(DateTime, nullable=True)
    publish_attempts = Column(Integer, nullable=False, default=0)
    next_publish_at = Column(DateTime, nullable=True)
    last_error = Column(String(300), nullable=True)


class ConsumerInbox(Base):
    """消费者收件箱：某个消费者已经成功处理过的事件。重复投递同一事件时据此跳过，保证幂等。"""
    __tablename__ = "consumer_inbox"
    __table_args__ = (UniqueConstraint("event_id", "consumer", name="uq_inbox_event_consumer"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), nullable=False)
    consumer = Column(String(60), nullable=False)
    processed_at = Column(DateTime, nullable=False, default=utcnow)


class ConsumerRetry(Base):
    """某个消费者对某个事件的处理租约与重试状态：领取时占位（防止多个进程重复处理），失败后记录次数与下次重试时间。"""
    __tablename__ = "consumer_retry"
    __table_args__ = (UniqueConstraint("event_id", "consumer", name="uq_retry_event_consumer"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), nullable=False)
    consumer = Column(String(60), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=False)
    last_error = Column(String(300), nullable=True)


class DeadLetter(Base):
    """死信：重试用尽的事件，等待人工处理（修复后重新投递，或写明原因后丢弃）。"""
    __tablename__ = "dead_letter"
    __table_args__ = (Index("idx_dead_letter_status", "status", "id"), Index("idx_dead_letter_event", "event_id", "consumer"))
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), nullable=False)
    consumer = Column(String(60), nullable=False)
    topic = Column(String(80), nullable=False)
    event_type = Column(String(60), nullable=False)
    trace_id = Column(String(64), nullable=True)
    payload_json = Column(Text, nullable=False)
    error = Column(String(500), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    status = Column(String(12), nullable=False, default="pending")      # pending / redelivered / discarded
    discard_reason = Column(String(500), nullable=True)
    handled_by = Column(Integer, nullable=True)
    handled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class OrchestrationPlan(Base):
    """跨部门协同办理：一段话拆成的多个部门步骤。只是计划，业务数据都由各步骤关联的 AI 工作成果经人工核对后写入。"""
    __tablename__ = "orchestration_plan"
    __table_args__ = (Index("idx_orch_plan_user", "user_id", "created_at"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_orch_plan_user"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id", name="fk_orch_plan_team"), nullable=False)
    source_text = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="open")   # open / closed
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class OrchestrationStep(Base):
    """协同计划的一步：交给哪个工作流/部门、依据哪段原文、关联哪份 AI 工作成果。"""
    __tablename__ = "orchestration_step"
    __table_args__ = (Index("idx_orch_step_plan", "plan_id", "seq"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("orchestration_plan.id", name="fk_orch_step_plan"), nullable=False)
    seq = Column(Integer, nullable=False)
    kind = Column(String(30), nullable=True)               # 工作流 ID；人事事项等没有工作流的为 None
    department_code = Column(String(20), nullable=True)    # 由哪类部门负责
    clause = Column(Text, nullable=False)
    reason = Column(String(300), nullable=False)
    state = Column(String(20), nullable=False, default="pending")  # pending / handoff / skipped / linked
    automation_work_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class EnterpriseLlmConnection(Base):
    """企业统一的模型连接：管理员按模型服务商（智谱 / OpenAI / DeepSeek …）配置一次 API Key，全公司共用。

    用户自己在“连接 AI 服务”里填的个人密钥优先；没有个人密钥时才用这里的。密钥加密保存，只写不读。"""
    __tablename__ = "enterprise_llm_connection"
    provider = Column(String(30), primary_key=True)
    api_key = Column(Text, nullable=False)              # Fernet 密文
    is_active = Column(Integer, nullable=False, default=1)
    updated_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class LLMConfig(Base):
    __tablename__ = "llm_config"
    __table_args__ = (
        Index("idx_llm_config_user_model", "user_id", "model_name"),
        Index("idx_llm_config_user_active", "user_id", "is_active"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    model_name = Column(String(100), nullable=False)  # "glm-4" / "gpt-4o" / "deepseek-chat"
    api_key = Column(String(500), nullable=False)      # 用户填写的 API Key（加密存储）
    api_url = Column(String(500))                      # 可选，自定义端点
    is_active = Column(Integer, default=1)             # 0=停用, 1=启用
# 聊天记录表
class Chat(Base):
    __tablename__ = "chat"
    __table_args__ = (
        Index("idx_chat_user_agent_time", "user_id", "agent_id", "create_time"),
    )
    id = Column(Integer,primary_key=True,autoincrement=True)
    user_id = Column(Integer,ForeignKey("user.id"))
    agent_id = Column(Integer,ForeignKey("agent.id"))
    question = Column(Text,nullable=False)
    answer = Column(Text,nullable=False)
    create_time = Column(DateTime,nullable=False)
# 工具表
class Tool(Base):
    __tablename__ = "tool"
    id = Column(Integer,primary_key=True,autoincrement=True)
    agent_id = Column(Integer,ForeignKey("agent.id"))
    tool_name = Column(String(255),nullable=False)
    tool_type = Column(String(255),nullable=False)
# 知识库文档表（上传的原始文档元数据）
class Knowledge(Base):
    __tablename__ = "knowledge"
    __table_args__ = (
        Index("idx_knowledge_user_created", "user_id", "created_at"),
        Index("idx_knowledge_agent_created", "agent_id", "created_at"),
        Index("idx_knowledge_agent_enabled_status", "agent_id", "is_enabled", "status"),
    )
    id  = Column(Integer ,primary_key=True,autoincrement=True)
    user_id = Column(Integer,ForeignKey("user.id",name="fk_knowledge_user"),nullable=False)
    # 知识库空间升级后：文档归属 space_id；agent_id 改为可空（存量文档保留旧值，空间上传的文档为 NULL）
    agent_id = Column(Integer,ForeignKey("agent.id",name="fk_knowledge_agent"),nullable=True)
    file_name = Column(String(255),nullable=False) # 原始文件名
    file_path = Column(String(500),nullable=False) #磁盘存储路径
    file_type = Column(String(50),nullable=False) # pdf/docx/txt/md
    file_size = Column(Integer,default=0) # 字节数
    chunk_count =Column(Integer,default=0) # 切块数（解析后回填）
    status = Column(String(20), default="pending")         # pending/processing/done/failed
    is_enabled = Column(Integer, default=1)                 # 0=禁用 1=启用，控制是否参与RAG检索
    error_msg = Column(Text, nullable=True)                 # 失败原因
    created_at = Column(DateTime,default=utcnow, nullable=False)
    # ---- 知识库空间升级（阶段1，加列不删旧列；agent_id 仍保留给旧路径与 legacy 向量集合）----
    space_id = Column(Integer, ForeignKey("knowledge_spaces.id", name="fk_knowledge_space"), nullable=True)
    category = Column(String(60), nullable=True)            # 文档分类
    tags_json = Column(Text, nullable=True)                 # ["制度","2024"]
    version = Column(String(40), nullable=True)             # 用户自填版本号
    source_type = Column(String(20), nullable=False, default="upload")  # upload / web / import
    source_url = Column(String(1000), nullable=True)        # web 抓取来源
    updated_at = Column(DateTime, nullable=True)
    chunk_size = Column(Integer, nullable=True)             # 用户自选切块大小（字符），NULL=用默认 RAG_CHUNK_SIZE


class KnowledgeSpace(Base):
    """企业知识库空间：可复用的知识库容器，Agent 通过 agent_knowledge_space 绑定。

    阶段1 为用户级隔离（user_id = owner）；team_id / organization_id 为阶段6 预留。
    统计字段（doc_count / chunk_count / last_indexed_at）由后台任务异步回填，不实时算。
    """
    __tablename__ = "knowledge_spaces"
    __table_args__ = (
        Index("idx_kspace_user_status", "user_id", "status"),
        Index("idx_kspace_team", "team_id"),
        Index("idx_kspace_org", "organization_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_kspace_user"), nullable=False)
    name = Column(String(120), nullable=False)
    description = Column(String(500), nullable=True)
    purpose = Column(String(60), nullable=True)             # customer_service / legal / product ...
    tags_json = Column(Text, nullable=True)
    is_enabled = Column(Integer, nullable=False, default=1)  # 0=停用 1=启用
    status = Column(String(20), nullable=False, default="active")   # active / archived
    doc_count = Column(Integer, nullable=False, default=0)
    chunk_count = Column(Integer, nullable=False, default=0)
    last_indexed_at = Column(DateTime, nullable=True)
    health_score = Column(Integer, nullable=True)           # 0~100，阶段5 回填
    health_json = Column(Text, nullable=True)
    # 迁移 / 企业预留
    legacy_agent_id = Column(Integer, nullable=True)        # 由某 Agent 私有库升级而来
    vector_migrated = Column(Integer, nullable=False, default=1)   # 0=检索需双读 legacy collection
    team_id = Column(Integer, ForeignKey("teams.id", name="fk_kspace_team"), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", name="fk_kspace_org"), nullable=True)
    # ---- Phase 3D 阶段1：密级（docs/enterprise-rbac-plan.md 9.5）----
    scope_type = Column(String(20), nullable=False, default="personal")   # personal/department/enterprise
    sensitivity = Column(String(20), nullable=False, default="internal")  # public/internal/confidential/restricted
    # ---- Phase 3D 阶段5：乐观锁字段，只加不接逻辑（同 Agent 那份注释）。没有发布
    # 生命周期列——空间已经有 status（active/archived），不是 draft/published 那一套。----
    row_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class KnowledgeSpaceDepartment(Base):
    """知识库空间 ↔ 部门：管理员把一个知识库“划分”给哪些部门（多对多）。

    scope_type = department 的空间，被划分到的部门的在职成员自动只读，部门负责人可编辑文档；
    scope_type = enterprise 的空间对全企业在职成员只读，不需要这张表；
    scope_type = personal 的空间还没有划分，只有所有者和被加入的成员能看到。
    “绝密”密级的空间不继承任何部门 / 全企业身份。"""
    __tablename__ = "knowledge_space_departments"
    __table_args__ = (
        Index("uq_kspace_department", "space_id", "team_id", unique=True),
        Index("idx_kspace_department_team", "team_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    space_id = Column(Integer, ForeignKey("knowledge_spaces.id", name="fk_ksd_space", ondelete="CASCADE"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id", name="fk_ksd_team", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class AgentKnowledgeSpace(Base):
    """Agent ↔ 知识库空间 多对多绑定。"""
    __tablename__ = "agent_knowledge_space"
    __table_args__ = (
        Index("uq_agent_space", "agent_id", "space_id", unique=True),
        Index("idx_aks_space", "space_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agent.id", name="fk_aks_agent"), nullable=False)
    space_id = Column(Integer, ForeignKey("knowledge_spaces.id", name="fk_aks_space"), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class RagDebugSample(Base):
    """知识库调试台：一次检索（可选带 LLM 回答）的完整快照。

    阶段4：用户在调试台跑一次检索后可「存为测试样例」，标注 useful/useless，
    勾选进评估集（in_eval_set=1）后可导出成 rag_eval 的 cases 喂给 /evaluation。
    """
    __tablename__ = "rag_debug_samples"
    __table_args__ = (
        Index("idx_rds_user_created", "user_id", "created_at"),
        Index("idx_rds_space", "space_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_rds_user"), nullable=False)
    space_id = Column(Integer, nullable=True)          # 单空间样例时记录；多空间留空
    space_ids_json = Column(Text, nullable=True)       # 实际检索用到的 space_id 列表
    agent_id = Column(Integer, nullable=True)          # 旧「Agent 私有库」调试时记录
    query = Column(Text, nullable=False)
    top_k = Column(Integer, nullable=True)
    rerank_enabled = Column(Integer, nullable=False, default=0)
    result_json = Column(Text, nullable=True)          # 命中 chunk / score / rerank / context / answer / citations 快照
    verdict = Column(String(10), nullable=True)        # useful / useless / null
    in_eval_set = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class SpaceMember(Base):
    """知识库空间成员（阶段6）。owner 由 knowledge_spaces.user_id 隐含，不落这张表。

    role ∈ admin / editor / viewer：
      viewer  只读；editor 可增删文档；admin 可改空间设置、管成员；owner 可删空间。
    """
    __tablename__ = "space_members"
    __table_args__ = (
        Index("uq_space_member", "space_id", "user_id", unique=True),
        Index("idx_space_member_user", "user_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    space_id = Column(Integer, ForeignKey("knowledge_spaces.id", name="fk_sm_space"), nullable=False)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_sm_user"), nullable=False)
    role = Column(String(20), nullable=False, default="viewer")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class KbAuditLog(Base):
    """知识库空间/文档/成员/绑定 的写操作审计（阶段6）。"""
    __tablename__ = "kb_audit_log"
    __table_args__ = (
        Index("idx_kb_audit_space_time", "space_id", "created_at"),
        Index("idx_kb_audit_user", "user_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)           # 操作人
    space_id = Column(Integer, nullable=True)
    action = Column(String(40), nullable=False)         # space.update / doc.upload / member.set ...
    target_type = Column(String(20), nullable=True)     # space / document / member / binding
    target_id = Column(Integer, nullable=True)
    detail = Column(Text, nullable=True)                # JSON 摘要
    created_at = Column(DateTime, default=utcnow, nullable=False)


class Plan(Base):
    """套餐：管理员配置，用户订阅其一。MVP 只做「每自然月 Token 用量」这一种配额资源

    （Agent 数 / 知识库空间数等其它维度的限额留作后续按需扩展）。
    monthly_token_limit=0 表示不限量。
    """
    __tablename__ = "plan"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False, unique=True)              # 程序标识，如 "free" / "pro"
    display_name = Column(String(100), nullable=False)                  # 展示名
    monthly_token_limit = Column(Integer, nullable=False, default=0)    # 0 = 不限量
    price_desc = Column(String(200), nullable=True)                     # 纯展示用价格说明，不接支付
    is_default = Column(Integer, nullable=False, default=0)             # 未订阅用户落到这个套餐（至多一条为 1）
    is_enabled = Column(Integer, nullable=False, default=1)             # 0=停用，停用后不可再被新订阅
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class UserSubscription(Base):
    """用户 ↔ 套餐 的当前绑定，一人一条。"""
    __tablename__ = "user_subscription"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_subscription_user"), nullable=False, unique=True)
    plan_id = Column(Integer, ForeignKey("plan.id", name="fk_subscription_plan"), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Organization(Base):
    """企业/组织。Phase 3B（docs/enterprise-rbac-plan.md）接入：单企业部署下长期只有 1 行，
    `organization_members` 表管实际成员和角色，`owner_user_id` 只是隐式创建者
    （跟 KnowledgeSpace.user_id 是 owner、SpaceMember 才是显式成员表的既有模式一致）。
    """
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    owner_user_id = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="active", server_default="active")  # active/disabled
    created_at = Column(DateTime, default=utcnow, nullable=False)


class Team(Base):
    """团队/部门。Phase 3B 接入：`team_members` 表管实际成员和角色。"""
    __tablename__ = "teams"
    __table_args__ = (Index("idx_team_org", "organization_id"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", name="fk_team_org"), nullable=True)
    name = Column(String(120), nullable=False)
    owner_user_id = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="active", server_default="active")  # active/disabled
    # 部门业务类型（hr/procurement/sales/finance/it，见 service/runtime/central_router.py
    # 的 VALID_DEPARTMENT_CODES）——部门工作台用这个字段决定显示哪个业务模块，不再靠
    # "这个部门有没有已发布的对应 Agent"反推，两者是独立的可用性判断。
    department_code = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class EnterpriseRole(Base):
    """企业/部门角色目录。scope 区分用在哪一层（organization/team），同一层内 code 唯一。

    初始数据由迁移插入，不是代码里硬编码判断：
      scope=organization: owner(3) / admin(2) / auditor(1) / member(0)
      scope=team:         admin(2) / editor(1) / member(0)
    `rank` 用于"至少要有 X 级"的判断，不用在代码里列举所有可能的角色名。
    """
    __tablename__ = "enterprise_role"
    __table_args__ = (
        Index("uq_enterprise_role_scope_code", "scope", "code", unique=True),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(20), nullable=False)   # organization / team
    code = Column(String(30), nullable=False)    # owner / admin / auditor / editor / member
    name = Column(String(60), nullable=False)    # 显示名，如"企业管理员"
    rank = Column(Integer, nullable=False, default=0)  # MySQL 8 保留字，手写原生 SQL 时记得加反引号
    created_at = Column(DateTime, default=utcnow, nullable=False)


class OrganizationMember(Base):
    """企业成员。role_id 必须指向 scope="organization" 的 EnterpriseRole 行——这条约束由
    service 层校验（service/enterprise_access.py），MySQL 的 CHECK 不能跨表，见设计稿 1.2 节。
    """
    __tablename__ = "organization_members"
    __table_args__ = (
        Index("uq_org_member", "organization_id", "user_id", unique=True),
        Index("idx_org_member_user", "user_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", name="fk_om_org"), nullable=False)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_om_user"), nullable=False)
    role_id = Column(Integer, ForeignKey("enterprise_role.id", name="fk_om_role"), nullable=False)
    status = Column(String(20), nullable=False, default="active")  # active / disabled
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class TeamMember(Base):
    """部门成员。一名用户只能属于一个部门；role_id 必须指向 scope="team" 的 EnterpriseRole 行。"""
    __tablename__ = "team_members"
    __table_args__ = (
        Index("uq_team_member_user", "user_id", unique=True),
        Index("idx_team_member_team", "team_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("teams.id", name="fk_tm_team"), nullable=False)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_tm_user"), nullable=False)
    role_id = Column(Integer, ForeignKey("enterprise_role.id", name="fk_tm_role"), nullable=False)
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ApprovalRequest(Base):
    """高风险操作的审批单（Phase 3D 阶段4，docs/enterprise-rbac-plan.md 9.5）。

    绑定具体的 (action, resource_type, resource_id)，不是一句笼统的"同意"——
    审批只对这一次这个资源的这个操作有效，过期或已消费（executed_at 不为空）
    后不能再复用。`service/approval_service.py` 是唯一的读写入口。
    """
    __tablename__ = "approval_request"
    __table_args__ = (
        Index("idx_approval_resource", "resource_type", "resource_id", "action", "status"),
        Index("idx_approval_applicant", "applicant_id"),
        UniqueConstraint("active_dedupe_key", name="uq_approval_active_dedupe"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    applicant_id = Column(Integer, ForeignKey("user.id", name="fk_approval_applicant"), nullable=False)
    approver_id = Column(Integer, ForeignKey("user.id", name="fk_approval_approver"), nullable=True)
    action = Column(String(60), nullable=False)          # 如 space.delete / skill.publish
    resource_type = Column(String(40), nullable=False)   # 如 space / skill / agent
    resource_id = Column(Integer, nullable=False)
    reason = Column(Text, nullable=True)
    # pending / approved / rejected / expired
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    # P1 并发修复（docs/enterprise-rbac-plan.md 相关记录）：同一个
    # (action, resource_type, resource_id) 同时只能有一条"活跃"（pending，或
    # approved 且未过期）审批单——应用层原来"先查一遍没有才插入"防不住两个并发
    # 请求同时通过检查、都插入一条，这里用真正的数据库唯一约束兜底，不靠应用层
    # 自己判断。这一列完全由 service/approval_service.py 维护：新建 pending 单时
    # 写成 f"{action}:{resource_type}:{resource_id}"，决定为 rejected 或发现已过期
    # 时清空成 NULL（approved 之后仍然算"活跃"，直到过期才清空，跟原有
    # `_ACTIVE_STATUSES` 的语义保持一致）。MySQL 的唯一索引允许多个 NULL 共存，
    # 只在非 NULL 值之间强制唯一，天然适合"只对活跃的那一条做唯一约束"这个需求，
    # 不需要 MySQL 不支持的"条件唯一索引"或生成列（生成列也没法引用 NOW()）。
    active_dedupe_key = Column(String(150), nullable=True)


class AuditEvent(Base):
    """通用写操作审计（Phase 3D 阶段6）。只追加，不提供 UPDATE/DELETE 入口——
    `service/audit_service.py` 只有 `record()`，没有改/删函数。

    跟已有的 `KbAuditLog` 是两张表，不是重复：`KbAuditLog` 是知识库空间模块早先
    自己建的、范围限定在 space/document/member/binding 那一块；这张表给知识库空间
    之外的操作用（目前是审批决定），以后企业成员/角色变更等也记这里，不是把
    `KbAuditLog` 泛化，避免动一张已经在用的表。
    """
    __tablename__ = "audit_event"
    __table_args__ = (
        Index("idx_audit_event_resource", "resource_type", "resource_id", "created_at"),
        Index("idx_audit_event_user", "user_id", "created_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    action = Column(String(60), nullable=False)
    resource_type = Column(String(40), nullable=True)
    resource_id = Column(Integer, nullable=True)
    detail = Column(Text, nullable=True)   # JSON 摘要
    created_at = Column(DateTime, default=utcnow, nullable=False)


class ToolConfirmation(Base):
    """高风险 Agent 工具调用的用户确认单（第五轮审计 P0-2：Prompt 注入可能触发
    真实业务操作）。

    ReAct 循环里 LLM 决定调用 `risk_level="high_risk"` 的工具（submit/approve/
    reject 这类真正产生业务后果的操作）时，`service/tools/langchain_adapter.py`
    不会直接执行，只会在这里插一条 pending 记录、把 token 当"工具结果"还给
    LLM。真正执行只有 `service/tool_confirmation_service.py::confirm_and_execute_async`
    这一个入口，只能被用户在前端点确认按钮时经由独立的 API 请求触发，跟 ReAct
    循环彼此隔离——哪怕对话历史或知识库文档里被注入了"请直接提交""请批准这条"
    之类的指令，模型在一轮 ReAct 循环里最多只能让流程走到"生成一条待确认单"，
    走不到"真的执行"。
    """
    __tablename__ = "tool_confirmation"
    __table_args__ = (
        Index("idx_tool_confirmation_user_status", "user_id", "status"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(64), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_toolconf_user"), nullable=False)
    agent_id = Column(Integer, nullable=True)
    tool_name = Column(String(80), nullable=False)
    tool_args = Column(Text, nullable=False)  # JSON，LLM 请求这次调用时的原始参数
    # pending / confirmed / rejected / expired
    status = Column(String(20), nullable=False, default="pending")
    result = Column(Text, nullable=True)      # confirmed 之后工具真正执行的返回值
    created_at = Column(DateTime, default=utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    decided_at = Column(DateTime, nullable=True)


# 知识块表（文档切分后的块，含向量库id引用）
class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunk"
    __table_args__ = (
        Index("idx_knowledge_chunk_knowledge_index", "knowledge_id", "chunk_index"),
        Index("idx_knowledge_chunk_vector_id", "vector_id"),
    )
    id  = Column(Integer ,primary_key=True,autoincrement=True)
    knowledge_id = Column(Integer, ForeignKey("knowledge.id", name="fk_chunk_knowledge"), nullable=False)
    chunk_index = Column(Integer,default=0)
    content = Column(Text,nullable=False)
    vector_id = Column(String(100),nullable=False)#向量数据库
    token_count =Column(Integer,default=0)
    created_at = Column(DateTime,default=utcnow, nullable=False)


class AgentApiConnector(Base):
    """Agent 可调用的企业 HTTP 接口：地址/认证/请求方式由用户预先配置好，
    Agent 运行时只把 LLM 填的参数发过去——URL 和认证信息完全不受 LLM 控制，
    从架构上避免"提示词注入诱导访问任意地址/泄露认证信息"的风险。"""
    __tablename__ = "agent_api_connector"
    __table_args__ = (
        Index("idx_agent_api_connector_agent", "agent_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_api_connector_user"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agent.id", name="fk_api_connector_agent"), nullable=False)
    name = Column(String(64), nullable=False)           # 工具名，会原样传给 LLM，需是合法标识符
    description = Column(String(500), nullable=False)   # 告诉 LLM 什么时候用、参数含义
    url = Column(String(1000), nullable=False)
    method = Column(String(10), nullable=False, default="GET")
    headers_encrypted = Column(Text, nullable=True)      # Fernet 加密（可能含 Authorization）
    param_schema_json = Column(Text, nullable=False)     # JSON Schema，同 BaseTool.get_parameters() 格式
    static_query_json = Column(Text, nullable=True)      # 固定附加的查询参数/请求体字段（不暴露给 LLM）
    is_enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class AgentExternalEndpoint(Base):
    """外部 Agent 服务的接入配置（Agent.runtime_type = external 时使用）。

    平台把对话按固定协议（docs/external-agent-protocol.md）转发到 url；每次请求都用
    secret_encrypted 里的密钥做 HMAC 签名，对方据此确认请求确实来自平台。
    地址、密钥、附加请求头都由管理员预先配置，模型和用户输入无法改变它们。"""
    __tablename__ = "agent_external_endpoint"
    __table_args__ = (
        UniqueConstraint("agent_id", name="uq_agent_external_endpoint_agent"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agent.id", name="fk_agent_external_endpoint_agent"), nullable=False)
    url = Column(String(1000), nullable=False)
    secret_encrypted = Column(Text, nullable=False)       # Fernet 加密的签名密钥
    headers_encrypted = Column(Text, nullable=True)       # Fernet 加密的附加请求头（可能含 Authorization）
    timeout_seconds = Column(Integer, nullable=False, default=60)
    send_knowledge = Column(Integer, nullable=False, default=0)   # 1 = 把检索到的资料片段一并发给对方（受密级策略约束）
    last_test_at = Column(DateTime, nullable=True)
    last_test_ok = Column(Integer, nullable=True)
    last_test_message = Column(String(300), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class EvalSet(Base):
    """固定评估集：一份可重复回归跑的问题集（question + 期望命中/答案），
    绑定某个 Agent 私有库或某个知识库空间。之前评估只能"这次请求带 cases 现算现返回"，
    没有地方沉淀，改完东西也没法知道效果是变好还是变差了。"""
    __tablename__ = "eval_set"
    __table_args__ = (
        Index("idx_eval_set_user_created", "user_id", "created_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_eval_set_user"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agent.id", name="fk_eval_set_agent"), nullable=True)
    space_id = Column(Integer, ForeignKey("knowledge_spaces.id", name="fk_eval_set_space"), nullable=True)
    name = Column(String(120), nullable=False)
    cases_json = Column(Text, nullable=False)      # 问题集本体：[{question, expected_*, answer, knowledge_id}, ...]
    settings_json = Column(Text, nullable=True)    # 跑评估时用的 top_k / rerank / 阈值等参数
    created_at = Column(DateTime, default=utcnow, nullable=False)


class EvalRun(Base):
    """评估集的一次运行结果快照，用于和上一轮自动比较（回归/变好了哪些问题）。"""
    __tablename__ = "eval_run"
    __table_args__ = (
        Index("idx_eval_run_set_created", "eval_set_id", "created_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    eval_set_id = Column(Integer, ForeignKey("eval_set.id", name="fk_eval_run_set"), nullable=False)
    hit_rate = Column(Float, nullable=True)
    recall = Column(Float, nullable=True)
    precision_at_k = Column(Float, nullable=True)
    mrr = Column(Float, nullable=True)
    faithfulness = Column(Float, nullable=True)
    report_json = Column(Text, nullable=False)     # 完整 report（含每条 case 明细），用于和上一轮 diff
    created_at = Column(DateTime, default=utcnow, nullable=False)


# Agent运行记录表（每次用户发消息=一次Run）
class AgentRun(Base):
    __tablename__ = "agent_run"
    __table_args__ = (
        Index("idx_agent_run_user_started", "user_id", "started_at"),
        Index("idx_agent_run_agent_started", "agent_id", "started_at"),
        Index("idx_agent_run_agent_conversation_started", "agent_id", "conversation_id", "started_at"),
        Index("idx_agent_run_status_started", "status", "started_at"),
        Index("idx_agent_run_agent_status", "agent_id", "status", "started_at"),       # 与迁移 20261007_0004 一致：按助手 + 状态看最近的运行
    )
    id  = Column(Integer ,primary_key=True,autoincrement=True)
    user_id = Column(Integer,ForeignKey("user.id",name="fk_run_user"),nullable=False)
    agent_id = Column(Integer,ForeignKey("agent.id",name="fk_run_agent"),nullable=False)
    chat_id = Column(Integer,ForeignKey("chat.id",name="fk_run_chat"),nullable=True)
    user_message = Column(Text, nullable=False)            # 用户原始问题
    final_answer = Column(Text, nullable=True)             # 最终回答（失败时为null）
    status = Column(String(20), default="running")         # running/finished/failed/max_iter
    total_steps = Column(Integer, default=0)     # 总步数
    total_tokens = Column(Integer, default=0)              # 总token消耗
    error_msg = Column(Text, nullable=True)                # 失败原因
    started_at = Column(DateTime, default=utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)          # 结束时间（结束时回填）
    conversation_id = Column(Integer, ForeignKey("conversation.id", name="fk_run_conv", ondelete="SET NULL"),nullable=True)
    trace_id = Column(String(64), nullable=True)           # 链路追踪编号：和日志、Java 业务服务、问题中心是同一个
    error_code = Column(String(60), nullable=True)         # 失败时的统一错误码（MODEL_TIMEOUT、JAVA_SERVICE_UNAVAILABLE…）
    issue_no = Column(String(40), nullable=True)           # 失败进入问题中心时的问题编号

# Agent运行步骤表（每一步的思考/工具/结果）
class AgentStep(Base):
    __tablename__ = "agent_step"
    __table_args__ = (
        Index("idx_agent_step_run_step", "run_id", "step_no"),
    )
    id  = Column(Integer ,primary_key=True,autoincrement=True)
    run_id = Column(Integer, ForeignKey("agent_run.id", name="fk_step_run"), nullable=False)
    step_no = Column(Integer, nullable=False)  # 第几步（从1开始）
    step_type = Column(String(20), nullable=False)  # planner/tool/responder
    thought = Column(Text, nullable=True)  # LLM思考内容
    tool_name = Column(String(100), nullable=True)  # 调用了哪个工具
    tool_args = Column(Text, nullable=True)  # 工具参数（JSON字符串）
    tool_result = Column(Text, nullable=True)  # 工具返回结果
    tokens = Column(Integer, default=0)  # 本步token消耗
    created_at = Column(DateTime, default=utcnow, nullable=False)


class BackgroundTask(Base):
    __tablename__ = "background_task"
    __table_args__ = (
        Index("idx_background_task_user_status_created", "user_id", "status", "created_at"),
        Index("idx_background_task_user_type_created", "user_id", "task_type", "created_at"),
        Index("idx_background_task_status_type_next_run", "status", "task_type", "next_run_at", "created_at", "id"),
        Index("idx_background_task_status_type_created", "status", "task_type", "created_at", "id"),
        Index("idx_background_task_status_started", "status", "started_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_task_user"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agent.id", name="fk_task_agent"), nullable=True)
    task_type = Column(String(50), nullable=False)
    # status: queued(排队) / running(执行中) / finished(成功) / failed(失败) / cancelled(已取消)
    status = Column(String(20), default="queued")
    title = Column(String(255), nullable=False)
    target_type = Column(String(50), nullable=True)
    target_id = Column(Integer, nullable=True)
    progress = Column(Integer, default=0)
    result = Column(Text, nullable=True)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)  # queued 任务的最早可领取时间，用于失败后延迟重试
    # 重试机制：retry_count 记录本任务被重试过几次；parent_task_id 指向触发本次重试的原任务
    retry_count = Column(Integer, default=0, nullable=False)
    parent_task_id = Column(Integer, ForeignKey("background_task.id", name="fk_task_parent"), nullable=True)


class WebMonitor(Base):
    """用户网页监控项：记录 URL、检查状态和最近一次内容指纹。"""
    __tablename__ = "web_monitor"
    __table_args__ = (
        Index("idx_web_monitor_user_active", "user_id", "is_active"),
        Index("idx_web_monitor_user_checked", "user_id", "last_checked_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_web_monitor_user"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agent.id", name="fk_web_monitor_agent"), nullable=True)
    name = Column(String(255), nullable=False)
    url = Column(String(1000), nullable=False)
    interval_minutes = Column(Integer, default=30, nullable=False)
    is_active = Column(Integer, default=1, nullable=False)
    last_status = Column(String(30), default="pending", nullable=False)
    last_hash = Column(String(64), nullable=True)
    last_title = Column(String(255), nullable=True)
    last_excerpt = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    last_change_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class OperationLog(Base):
    __tablename__ = "operation_log"
    __table_args__ = (
        Index("idx_operation_log_created", "created_at"),
        Index("idx_operation_log_user_created", "user_id", "created_at"),
        Index("idx_operation_log_method_created", "method", "created_at"),
        Index("idx_operation_log_status_created", "status_code", "created_at"),
        Index("idx_operation_log_latency_created", "latency_ms", "created_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_operation_log_user"), nullable=True)
    username = Column(String(255), nullable=True)
    method = Column(String(10), nullable=False)
    path = Column(String(500), nullable=False)
    status_code = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    client_ip = Column(String(100), nullable=True)
    user_agent = Column(String(500), nullable=True)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class Skill(Base):
    __tablename__ = "skill"
    __table_args__ = (
        Index("idx_skill_user_id", "user_id"),
        Index("idx_skill_public", "is_public"),
        Index("idx_skill_org", "organization_id"),
        Index("idx_skill_team", "team_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)      # 创建者
    name = Column(String(255), nullable=False)                            # 技能名
    description = Column(String(500))                                     # 描述
    config_file = Column(String(500), nullable=False)                     # YML路径
    is_public = Column(Integer, default=0)                                # 0=私有 1=公开
    created_at = Column(DateTime, default=utcnow)
    # ---- Phase 3D 阶段1：资源归属与密级（docs/enterprise-rbac-plan.md 9.5）----
    organization_id = Column(Integer, ForeignKey("organizations.id", name="fk_skill_org"), nullable=True)
    team_id = Column(Integer, ForeignKey("teams.id", name="fk_skill_team"), nullable=True)
    scope_type = Column(String(20), nullable=False, default="personal")   # personal/department/enterprise
    sensitivity = Column(String(20), nullable=False, default="internal")  # public/internal/confidential/restricted
    # ---- Phase 3D 阶段5：乐观锁 + 发布生命周期字段，只加字段不接逻辑（同 Agent 那份注释）----
    row_version = Column(Integer, nullable=False, default=0)
    lifecycle_status = Column(String(20), nullable=False, default="draft")  # draft/reviewing/published/retired
    # 被哪些Agent使用（多对多） ← 新增这 3 行
    agents: Mapped[List["Agent"]] = relationship(
        secondary="agent_skill", lazy=False, back_populates="skills"
    )

class SkillVersion(Base):
    """技能配置的历史快照：每次编辑前自动存一份，可以恢复到任意一版。

    所有用户绑定的是同一份技能配置，管理员改错会影响所有人，所以必须有回滚。
    不加外键：删除技能时由 service 先清掉它的快照。
    """
    __tablename__ = "skill_version"
    __table_args__ = (Index("idx_skill_version_skill", "skill_id", "version_no"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(Integer, nullable=False)
    version_no = Column(Integer, nullable=False)                          # 该技能下从 1 递增
    name = Column(String(255), nullable=False)
    description = Column(String(500))
    config_text = Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False)   # 运行时 YML 原文
    note = Column(String(200))                                            # 为什么存这一版
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)


# Agent-Skill 多对多关联(一个Agent可用多个Skill,一个Skill可被多个Agent用)
agent_skill = Table(
    "agent_skill",
    Base.metadata,
    Column("agent_id", Integer, ForeignKey("agent.id"), primary_key=True),
    Column("skill_id", Integer, ForeignKey("skill.id"), primary_key=True)
)

# Memory 记忆表（长期记忆：会话摘要 + 关键事实）
class Memory(Base):
    __tablename__ = "memory"
    __table_args__ = (
        Index("idx_memory_user_agent_type_created", "user_id", "agent_id", "memory_type", "created_at"),
        Index("idx_memory_agent_id", "agent_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_memory_user"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agent.id", name="fk_memory_agent"), nullable=False)
    memory_type = Column(String(20), nullable=False)  # summary=会话摘要, fact=关键事实
    content = Column(Text, nullable=False)
    chat_count = Column(Integer, default=0)  # 生成这条记忆时有多少轮对话
    created_at = Column(DateTime, default=utcnow, nullable=False)
# ========== 会话系统 ==========
class Conversation(Base):
    """会话表：一个 Agent 下可以有多个会话，每个会话包含多条消息"""
    __tablename__ = "conversation"
    __table_args__ = (
        Index("idx_conversation_user_agent_flags_time", "user_id", "agent_id", "is_archived", "is_pinned", "update_time"),
        Index("idx_conversation_user_time", "user_id", "update_time"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_conv_user"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agent.id", name="fk_conv_agent"), nullable=False)
    title = Column(String(255), default="新会话")        # 会话标题（可由首条消息自动生成）
    is_pinned = Column(Integer, default=0)                # 0=普通 1=置顶
    is_archived = Column(Integer, default=0)              # 0=正常 1=归档
    create_time = Column(DateTime, default=utcnow, nullable=False)
    update_time = Column(DateTime, default=utcnow, nullable=False)  # 最后一条消息时间
    # 关联消息（一对多）
    messages: Mapped[List["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",   # 删除会话时级联删除消息
        order_by="Message.create_time",  # 按时间排序
        lazy=True
    )

class Message(Base):
    """消息表：一个会话下的每条消息（user/assistant/system）"""
    __tablename__ = "message"
    __table_args__ = (
        Index("idx_message_conversation_time", "conversation_id", "create_time"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversation.id", name="fk_msg_conv"), nullable=False)
    role = Column(String(20), nullable=False)    # user / assistant / system
    content = Column(Text, nullable=False)
    create_time = Column(DateTime, default=utcnow, nullable=False)
    # 反向关联会话
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class UserWidget(Base):
    """用户自定义工作台小窗口。

    组件不保存前端源码，只保存一份「配置」：五段式 data_source / processor /
    view / trigger / actions，由统一的运行引擎（service/widgets/runner.py）执行、
    由前端统一渲染器按 view.kind 渲染。新增数据源/处理器/视图只需注册，不改主流程。
    """
    __tablename__ = "user_widgets"
    __table_args__ = (
        Index("idx_user_widgets_user_sort", "user_id", "sort_order"),
        Index("idx_user_widgets_next_run", "enabled", "next_run_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_user_widgets_user"), nullable=False)
    name = Column(String(120), nullable=False)
    type = Column(String(40), nullable=False)                 # 白名单组件类型
    description = Column(Text, nullable=True)
    spec_version = Column(Integer, nullable=False, default=1)  # 组件协议版本，便于日后升级兼容
    capabilities_json = Column(Text, nullable=True)            # ["fetch","schedule",...]
    data_source_json = Column(Text, nullable=True)             # {"kind": "...", "config": {...}}
    processor_json = Column(Text, nullable=True)
    view_json = Column(Text, nullable=True)
    trigger_json = Column(Text, nullable=True)
    actions_json = Column(Text, nullable=True)                 # ["refresh","edit","hide","delete"]
    enabled = Column(Integer, nullable=False, default=1)       # 0=隐藏 1=显示
    sort_order = Column(Integer, nullable=False, default=0)
    # 调度状态（P1 不单独建 widget_schedules 表，规则存在 trigger_json，运行状态放这里）
    next_run_at = Column(DateTime, nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    last_status = Column(String(20), nullable=True)            # ok / error
    fail_count = Column(Integer, nullable=False, default=0)
    last_alert_level = Column(String(10), nullable=True)        # ok/warn/alert，用于外部告警推送去重（只在等级变化时推）
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class NotificationChannel(Base):
    """用户配置的外部告警推送通道。

    目前只做通用 Webhook（飞书/钉钉/企业微信/Slack 自定义机器人、或用户自建接收端，
    本质都是一个接受 JSON POST 的 URL），不做邮件/短信——那些需要额外的发信基础设施，
    Webhook 零依赖就能覆盖国内最常用的群机器人场景，先把「组件阈值告警能推到群里」这个
    最高频需求做完整。
    """
    __tablename__ = "notification_channel"
    __table_args__ = (
        Index("idx_notification_channel_user", "user_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_notification_channel_user"), nullable=False)
    name = Column(String(80), nullable=False)
    kind = Column(String(20), nullable=False, default="webhook")   # 目前只有 webhook，预留扩展
    webhook_url = Column(String(1000), nullable=False)
    is_enabled = Column(Integer, nullable=False, default=1)
    last_sent_at = Column(DateTime, nullable=True)
    last_error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class WorkItem(Base):
    """统一待办：提醒规则生成的、AI 工作成果里的后续事项、以及用户自己记的待办。
    source_key 在同一负责人下唯一，规则重复运行不会重复创建；条件不再成立时由规则自动关闭。"""
    __tablename__ = "work_item"
    __table_args__ = (
        UniqueConstraint("user_id", "source_key", name="uq_work_item_source"),
        Index("idx_work_item_owner_status", "user_id", "status", "due_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_work_item_user"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id", name="fk_work_item_team"), nullable=True)
    source_type = Column(String(30), nullable=False)        # reminder / automation / manual
    source_key = Column(String(120), nullable=False)
    rule = Column(String(40), nullable=True)                # 生成它的提醒规则
    title = Column(String(200), nullable=False)
    detail = Column(String(500), nullable=True)
    link = Column(String(200), nullable=True)
    priority = Column(String(10), nullable=False, default="normal")  # low/normal/high
    status = Column(String(12), nullable=False, default="open")      # open/done/dismissed
    due_at = Column(DateTime, nullable=True)
    resolved_by = Column(String(12), nullable=True)         # user / rule
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Notification(Base):
    """站内通知。dedupe_key 在同一用户下唯一，同一件事只通知一次。"""
    __tablename__ = "notification"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notification_dedupe"),
        Index("idx_notification_user_read", "user_id", "read_at", "created_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_notification_user"), nullable=False)
    category = Column(String(30), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(String(500), nullable=True)
    link = Column(String(200), nullable=True)
    dedupe_key = Column(String(160), nullable=False)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class NotificationPreference(Base):
    """通知偏好：静音的类别（不再产生站内通知和外部推送）、免打扰时段（只影响外部推送）。"""
    __tablename__ = "notification_preference"
    user_id = Column(Integer, ForeignKey("user.id", name="fk_notification_pref_user"), primary_key=True)
    muted_categories = Column(Text, nullable=False, default="[]")
    quiet_start = Column(String(5), nullable=True)   # "22:00"（北京时间）
    quiet_end = Column(String(5), nullable=True)     # "08:00"
    push_external = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ReminderRun(Base):
    """每条提醒规则的运行租约与健康状态：多个 Worker 同时运行时只有拿到租约的那个执行。"""
    __tablename__ = "reminder_run"
    rule = Column(String(40), primary_key=True)
    lease_until = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_started_at = Column(DateTime, nullable=True)
    last_finished_at = Column(DateTime, nullable=True)
    last_status = Column(String(12), nullable=True)   # ok / failed
    last_error = Column(String(500), nullable=True)
    last_created = Column(Integer, nullable=False, default=0)
    last_resolved = Column(Integer, nullable=False, default=0)
    consecutive_failures = Column(Integer, nullable=False, default=0)


class BootstrapMarker(Base):
    """一次性初始化的哨兵：某件事（如“自动建企业”）做过就留一行。主键保证多个进程同时初始化时只有一个能写进去，
    其余的撞主键后回滚（命名锁之外数据库层面的兜底，见 service/enterprise_bootstrap.py）。"""
    __tablename__ = "bootstrap_marker"
    name = Column(String(60), primary_key=True)
    done_at = Column(DateTime, nullable=False, default=utcnow)
    detail = Column(String(200), nullable=True)


class CollaborationApp(Base):
    """企业在飞书 / 钉钉上建的自建应用（机器人）。密钥、加密 Key 只存密文，页面和接口都不回显。"""
    __tablename__ = "collaboration_app"
    __table_args__ = (UniqueConstraint("organization_id", "provider", name="uq_collaboration_app"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False)
    provider = Column(String(20), nullable=False)                 # feishu / dingtalk
    app_id = Column(String(120), nullable=False)                  # 飞书 App ID；钉钉 AppKey（ClientId）
    encrypted_app_secret = Column(Text, nullable=False)
    # 以 Fernet 密文保存。历史版本曾经明文保存；service/integrations/apps.py 在首次读取时会自动迁移。
    verification_token = Column(Text, nullable=True)              # 飞书 Verification Token；钉钉事件订阅的签名 token
    encrypted_encrypt_key = Column(Text, nullable=True)           # 飞书 Encrypt Key；钉钉事件订阅的 aes_key
    robot_code = Column(String(120), nullable=True)               # 钉钉机器人 robotCode（主动发消息用）
    card_template_id = Column(String(120), nullable=True)         # 钉钉互动卡片模板（按钮回调需要）
    enabled = Column(Integer, nullable=False, default=0)
    last_health_at = Column(DateTime, nullable=True)
    last_error = Column(String(500), nullable=True)
    updated_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class ExternalUserBinding(Base):
    """外部平台的人 ↔ 平台账号。只有绑定了、状态是 active 的人，才能在飞书 / 钉钉里用助手。"""
    __tablename__ = "external_user_binding"
    __table_args__ = (
        UniqueConstraint("provider", "external_tenant_id", "external_user_id", name="uq_external_user"),
        Index("idx_external_user_local", "local_user_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False)
    provider = Column(String(20), nullable=False)
    external_tenant_id = Column(String(120), nullable=False)      # 飞书 tenant_key；钉钉 corpId
    external_user_id = Column(String(120), nullable=False)        # 飞书 open_id；钉钉 userid（staffId）
    external_union_id = Column(String(120), nullable=True)
    external_name = Column(String(120), nullable=True)
    local_user_id = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="active")  # active / disabled（离职、被管理员停用）/ unmatched（同步到了但没对上账号）
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ExternalDepartmentBinding(Base):
    """外部平台的部门 ↔ 平台部门。同步组织架构时按名称对上，对不上的留给管理员处理。"""
    __tablename__ = "external_department_binding"
    __table_args__ = (UniqueConstraint("provider", "external_tenant_id", "external_department_id", name="uq_external_department"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False)
    provider = Column(String(20), nullable=False)
    external_tenant_id = Column(String(120), nullable=False)
    external_department_id = Column(String(120), nullable=False)
    external_name = Column(String(200), nullable=True)
    external_parent_id = Column(String(120), nullable=True)
    local_team_id = Column(Integer, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)


class ExternalBindCode(Base):
    """员工自助绑定飞书 / 钉钉账号用的一次性绑定码：员工在平台设置里领取，在飞书 / 钉钉里发给机器人“绑定 123456”。
    只存 HMAC 摘要（code_hash），不存明文；10 分钟过期，只能用一次；同一员工同一平台领新码时旧码作废。"""
    __tablename__ = "external_bind_code"
    __table_args__ = (
        Index("idx_external_bind_code_hash", "provider", "code_hash"),
        Index("idx_external_bind_code_user", "user_id", "provider"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(20), nullable=False)
    user_id = Column(Integer, nullable=False)
    code_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    used_by_external_id = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ExternalEventInbox(Base):
    """收到的外部回调：(provider, tenant_id, event_id) 唯一，飞书 / 钉钉重复推送同一个事件只处理一次，
    不会因为重试建出两张单子、两条跟进或两次报销。"""
    __tablename__ = "external_event_inbox"
    __table_args__ = (
        UniqueConstraint("provider", "tenant_id", "event_id", name="uq_external_event"),
        Index("idx_external_event_status", "status", "received_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(20), nullable=False)
    tenant_id = Column(String(120), nullable=False)
    event_id = Column(String(120), nullable=False)
    event_type = Column(String(80), nullable=False)
    status = Column(String(20), nullable=False, default="received")   # received / processing / done / ignored / failed
    received_at = Column(DateTime, nullable=False, default=utcnow)
    processed_at = Column(DateTime, nullable=True)
    trace_id = Column(String(64), nullable=True)
    last_error = Column(String(500), nullable=True)


class CustomerActivity(Base):
    """CRM 客户活动：邮件、会议、群聊、电话纪要、人工跟进、报价、待办……统一放一张表，客户时间线按时间展示。

    (source_provider, external_source_id) 唯一：同一封邮件（Message-ID）、同一个会议（UID）、同一条飞书 / 钉钉消息
    只会生成一条活动。customer_id 为空表示还没对上客户（match_status=pending 等销售选择，unmatched 没有候选）。"""
    __tablename__ = "customer_activity"
    __table_args__ = (
        UniqueConstraint("source_provider", "external_source_id", name="uq_customer_activity_source"),
        Index("idx_customer_activity_customer", "customer_id", "occurred_at"),
        Index("idx_customer_activity_team_match", "team_id", "match_status"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, nullable=False)
    customer_id = Column(Integer, nullable=True)
    activity_type = Column(String(20), nullable=False)        # email / meeting / chat / call / followup / quote / todo
    source_provider = Column(String(20), nullable=False)      # email / imap / calendar / feishu / dingtalk / manual
    external_source_id = Column(String(255), nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    participants_json = Column(Text, nullable=True)
    title = Column(String(300), nullable=True)
    content = Column(LONGTEXT, nullable=True)
    summary = Column(Text, nullable=True)
    created_by = Column(Integer, nullable=False)
    trace_id = Column(String(64), nullable=True)
    match_status = Column(String(20), nullable=False, default="auto")   # explicit / auto / manual / pending / unmatched / ignored
    match_confidence = Column(Float, nullable=True)
    match_method = Column(String(30), nullable=True)
    match_candidates_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class CrmCustomerAlias(Base):
    """销售手动指定“这封邮件 / 这个人属于哪个客户”后记下的对应关系（邮箱、手机号、邮箱域名、公司别名），
    下次同样的来源直接对上，越用越准。按部门隔离。"""
    __tablename__ = "crm_customer_alias"
    __table_args__ = (UniqueConstraint("team_id", "alias_type", "alias_value", name="uq_crm_customer_alias"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, nullable=False)
    alias_type = Column(String(20), nullable=False)           # email / phone / domain / name
    alias_value = Column(String(255), nullable=False)
    customer_id = Column(Integer, nullable=False)
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class CustomerSummarySnapshot(Base):
    """客户摘要（增量生成：上一版摘要 + 之后的新活动 → 新摘要，不把全部历史再送一遍模型）。保留历史版本。"""
    __tablename__ = "customer_summary_snapshot"
    __table_args__ = (Index("idx_customer_summary_customer", "team_id", "customer_id", "id"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, nullable=False)
    customer_id = Column(Integer, nullable=False)
    summary = Column(Text, nullable=False)
    needs_json = Column(Text, nullable=True)
    stakeholders_json = Column(Text, nullable=True)
    risks_json = Column(Text, nullable=True)
    next_actions_json = Column(Text, nullable=True)
    based_on_activity_id = Column(Integer, nullable=True)     # 摘要已经看过的最后一条活动
    generated_at = Column(DateTime, nullable=False, default=utcnow)
    model_name = Column(String(100), nullable=True)
    generated_by = Column(Integer, nullable=True)


class CrmOpportunitySnapshot(Base):
    """商机变化记录（阶段、金额、预计成交日期）：只在发生变化时记一条，用来判断金额下降、多次延期、阶段变化。"""
    __tablename__ = "crm_opportunity_snapshot"
    __table_args__ = (Index("idx_crm_opp_snapshot", "opportunity_id", "id"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, nullable=False)
    customer_id = Column(Integer, nullable=False)
    opportunity_id = Column(Integer, nullable=False)
    stage = Column(String(20), nullable=False)
    amount = Column(String(40), nullable=False)
    expected_close_date = Column(String(10), nullable=True)
    captured_at = Column(DateTime, nullable=False, default=utcnow)


class CrmRiskFinding(Base):
    """商机 / 客户风险。每条必须带证据（evidence）。同一客户、同一商机、同一风险只有一条：
    再次扫描仍存在就更新 last_seen_at，消失了就标 resolved，不会重复生成。"""
    __tablename__ = "crm_risk_finding"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_crm_risk_dedupe"),
        Index("idx_crm_risk_team", "team_id", "status"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, nullable=False)
    customer_id = Column(Integer, nullable=False)
    opportunity_id = Column(Integer, nullable=True)
    risk_code = Column(String(40), nullable=False)
    level = Column(String(10), nullable=False)               # high / medium / low
    evidence = Column(Text, nullable=False)
    suggested_action = Column(String(500), nullable=True)
    source = Column(String(10), nullable=False, default="rule")   # rule / model
    source_activity_id = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="open")   # open / resolved
    dedupe_key = Column(String(200), nullable=False)
    first_seen_at = Column(DateTime, nullable=False, default=utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=utcnow)
    resolved_at = Column(DateTime, nullable=True)


class CrmActionSuggestion(Base):
    """Agent 给出的下一步建议。只是建议：销售选择“确认创建任务 / 修改后创建 / 忽略 / 稍后提醒”，
    确认后才进待办中心；不会自动改商机、不会自动联系客户。同一个来源（同一条风险）只生成一条建议。"""
    __tablename__ = "crm_action_suggestion"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_crm_action_dedupe"),
        Index("idx_crm_action_team", "team_id", "status"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, nullable=False)
    customer_id = Column(Integer, nullable=False)
    opportunity_id = Column(Integer, nullable=True)
    risk_finding_id = Column(Integer, nullable=True)
    title = Column(String(300), nullable=False)
    detail = Column(Text, nullable=True)
    due_date = Column(String(10), nullable=True)
    status = Column(String(20), nullable=False, default="suggested")   # suggested / created / ignored / snoozed
    remind_at = Column(DateTime, nullable=True)
    work_item_key = Column(String(120), nullable=True)
    decision = Column(String(20), nullable=True)             # create / edit_create / ignore / snooze（统计“原样采纳”用）
    dedupe_key = Column(String(200), nullable=False)
    decided_by = Column(Integer, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class CrmMailAccount(Base):
    """员工连接的邮箱（IMAP，Microsoft 365 / Gmail / 企业邮箱都支持）。只读取指定的文件夹（默认“CRM”）：
    员工把要进 CRM 的邮件转发或移动到这个文件夹，不读收件箱里的其他邮件。密码 / 授权码加密保存。"""
    __tablename__ = "crm_mail_account"
    __table_args__ = (UniqueConstraint("user_id", "team_id", name="uq_crm_mail_account"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    team_id = Column(Integer, nullable=False)
    imap_host = Column(String(200), nullable=False)
    imap_port = Column(Integer, nullable=False, default=993)
    username = Column(String(200), nullable=False)
    encrypted_password = Column(Text, nullable=False)
    folder = Column(String(100), nullable=False, default="CRM")
    last_uid = Column(Integer, nullable=False, default=0)
    last_synced_at = Column(DateTime, nullable=True)
    last_error = Column(String(500), nullable=True)
    enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class InvoiceExtraction(Base):
    """发票识别结果（图片 / PDF → 结构化字段）。每个字段都有置信度（field_confidence_json）；
    低于阈值的字段必须员工逐项确认后才能用于报销（status: needs_review → confirmed）。
    file_sha256 用来发现同一个文件重复上传；invoice_number 用来发现同一张发票重复报销。"""
    __tablename__ = "invoice_extraction"
    __table_args__ = (
        Index("idx_invoice_extraction_user", "user_id", "id"),
        Index("idx_invoice_extraction_hash", "file_sha256"),
        Index("idx_invoice_extraction_number", "invoice_number"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    team_id = Column(Integer, nullable=False)
    file_name = Column(String(255), nullable=True)
    file_sha256 = Column(String(64), nullable=False)
    invoice_type = Column(String(40), nullable=True)
    invoice_code = Column(String(20), nullable=True)
    invoice_number = Column(String(30), nullable=True)
    issued_at = Column(String(10), nullable=True)
    seller_name = Column(String(200), nullable=True)
    seller_tax_id = Column(String(30), nullable=True)
    buyer_name = Column(String(200), nullable=True)
    buyer_tax_id = Column(String(30), nullable=True)
    amount_without_tax = Column(String(20), nullable=True)
    tax_amount = Column(String(20), nullable=True)
    total_amount = Column(String(20), nullable=True)
    currency = Column(String(10), nullable=True, default="CNY")
    confidence = Column(Float, nullable=True)                 # 整体置信度（最低的那个字段）
    field_confidence_json = Column(Text, nullable=True)       # {字段: 置信度}
    checks_json = Column(Text, nullable=True)                 # 校验结果 [{level, code, text}]
    corrected_fields_json = Column(Text, nullable=True)       # 员工改过的字段
    raw_reference = Column(Text, nullable=True)               # 识别依据的原文片段
    method = Column(String(20), nullable=True)                # text / ocr / model
    status = Column(String(20), nullable=False, default="needs_review")   # needs_review / confirmed / used / discarded
    claim_id = Column(Integer, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ExpensePolicyRule(Base):
    """费用标准（结构化，不写进提示词）：某类费用在某级城市、某职级的单笔上限、是否必须有发票、超标后谁审批。
    字段为空表示“不限”。生效区间内、条件最具体的一条生效。"""
    __tablename__ = "expense_policy_rule"
    __table_args__ = (Index("idx_expense_policy_org", "organization_id", "category"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False)
    category = Column(String(20), nullable=False)             # TRAVEL / MEAL / OFFICE_SUPPLY / TRANSPORT / OTHER
    city_level = Column(String(10), nullable=True)            # tier1 / tier2 / other；空 = 不限
    employee_level = Column(String(20), nullable=True)        # staff / manager；空 = 不限
    amount_limit = Column(String(20), nullable=True)          # 单笔上限；空 = 不限额
    receipt_required = Column(Integer, nullable=False, default=1)
    approval_level = Column(String(20), nullable=False, default="team_admin")   # 超标后需要的审批：team_admin / finance / org_admin
    effective_from = Column(String(10), nullable=True)
    effective_to = Column(String(10), nullable=True)
    note = Column(String(300), nullable=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ErpExport(Base):
    """记账凭证推送到 ERP 的记录。voucher_id 唯一：同一张凭证不管点几次、重试几次，ERP 只会收到同一个幂等键，
    推送成功后不再重复推送。"""
    __tablename__ = "erp_export"
    __table_args__ = (UniqueConstraint("voucher_id", name="uq_erp_export_voucher"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False)
    voucher_id = Column(Integer, nullable=False)
    idempotency_key = Column(String(80), nullable=False)
    status = Column(String(20), nullable=False, default="pending")   # pending / sent / failed
    attempts = Column(Integer, nullable=False, default=0)
    erp_document_id = Column(String(120), nullable=True)
    last_error = Column(String(500), nullable=True)
    requested_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    sent_at = Column(DateTime, nullable=True)


class ItSelfServiceSession(Base):
    """IT 自助：员工描述问题 → 推荐知识文章 → 员工明确点“已解决”或“没有解决，创建工单”。
    只看了文章不算解决；只有 confirmed_solved=1 才计入自助解决率。转成工单的记下工单号；
    解决后 7 天内同一问题又来报修、或转出的工单被重开，记为重新打开（影响文章质量指标）。"""
    __tablename__ = "it_self_service_session"
    __table_args__ = (
        Index("idx_it_session_user", "user_id", "started_at"),
        Index("idx_it_session_ticket", "converted_ticket_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    team_id = Column(Integer, nullable=True)
    question = Column(Text, nullable=False)
    classification = Column(String(30), nullable=True)
    article_ids_json = Column(Text, nullable=True)
    suggestion = Column(Text, nullable=True)
    confirmed_solved = Column(Integer, nullable=True)         # null 未反馈 / 1 已解决 / 0 没解决
    solved_article_id = Column(Integer, nullable=True)
    converted_ticket_id = Column(Integer, nullable=True)
    reopened = Column(Integer, nullable=False, default=0)
    reopened_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=False, default=utcnow)
    feedback_at = Column(DateTime, nullable=True)


class ItArticleFeedback(Base):
    """员工对推荐文章的操作：打开看过（viewed_at）和评价（helpful 1 有用 / 0 没用）。每个会话每篇文章一条。"""
    __tablename__ = "it_article_feedback"
    __table_args__ = (UniqueConstraint("session_id", "article_id", name="uq_it_article_feedback"),
                      Index("idx_it_article_feedback_article", "article_id"))
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, nullable=False)
    article_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    viewed_at = Column(DateTime, nullable=True)
    helpful = Column(Integer, nullable=True)
    rated_at = Column(DateTime, nullable=True)


class ProductivityFact(Base):
    """统一提效事实：每件经过 AI 的工作（整理成果、助手办理的业务、发票识别、CRM 建议、IT 自助）和每次 Agent 运行一条。
    由 service/productivity_service.py 从各来源表整理而来，source_key 唯一（重复整理不重复计数）。
    三种时间分开存：saved_minutes 是按基准估算的节省；agent_seconds + review_seconds 是实测用时；
    reported_saved_minutes 是员工自己反馈的节省（整理时不会被覆盖）。"""
    __tablename__ = "productivity_fact"
    __table_args__ = (
        UniqueConstraint("source_key", name="uq_productivity_fact_source"),
        Index("idx_productivity_fact_team", "team_id", "started_at"),
        Index("idx_productivity_fact_org", "organization_id", "started_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_key = Column(String(120), nullable=False)
    organization_id = Column(Integer, nullable=True)
    team_id = Column(Integer, nullable=True)
    user_id = Column(Integer, nullable=True)
    agent_id = Column(Integer, nullable=True)
    workflow_type = Column(String(60), nullable=False)
    business_object_type = Column(String(40), nullable=True)
    business_object_id = Column(String(120), nullable=True)
    source_type = Column(String(30), nullable=False)          # automation / tool_confirm / invoice / crm_suggestion / it_self_service / agent_run
    baseline_minutes = Column(Float, nullable=False, default=0)
    agent_seconds = Column(Float, nullable=False, default=0)
    review_seconds = Column(Float, nullable=False, default=0)
    saved_minutes = Column(Float, nullable=False, default=0)
    reported_saved_minutes = Column(Float, nullable=True)
    draft_created = Column(Integer, nullable=False, default=0)
    draft_adopted = Column(Integer, nullable=False, default=0)
    adopted_as_is = Column(Integer, nullable=False, default=0)
    business_initiated = Column(Integer, nullable=False, default=0)
    completed = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    failure_code = Column(String(60), nullable=True)
    fields_changed_json = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class AgentPipeline(Base):
    """Agent 流水线：把多个 Agent 串成一条固定顺序的处理链——上一步的回答自动作为

    下一步的输入消息。范围有意收窄成「线性串行」，不做分支/条件/并行这些真正的
    工作流引擎才需要的复杂度：多数人的诉求是"先用 A 处理一遍，再让 B 精加工"这种
    简单串联，值不值得上完整 DAG 编排，等有真实需求信号再说。
    """
    __tablename__ = "agent_pipeline"
    __table_args__ = (
        Index("idx_agent_pipeline_user", "user_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_pipeline_user"), nullable=False)
    name = Column(String(120), nullable=False)
    description = Column(String(500), nullable=True)
    steps_json = Column(Text, nullable=False)   # [{"agent_id": 1, "label": "第一步"}, ...]
    is_enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class WidgetDataPoint(Base):
    """组件每次运行的结果快照。

    payload_json 是通用结构，chart / table / metric / markdown 等各种展示形态
    都往里放；label / value / recorded_at 是通用快速查询字段（时间序列、趋势）。
    """
    __tablename__ = "widget_data_points"
    __table_args__ = (
        Index("idx_widget_data_points_widget_time", "widget_id", "recorded_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    widget_id = Column(Integer, ForeignKey("user_widgets.id", name="fk_widget_data_points_widget"), nullable=False)
    recorded_at = Column(DateTime, default=utcnow, nullable=False)
    ok = Column(Integer, nullable=False, default=1)            # 1=成功 0=失败
    label = Column(String(255), nullable=True)                # 例如 "1893.2 USD/oz"
    value = Column(Float, nullable=True)                      # 可用于快速取最新数值/画趋势
    payload_json = Column(Text, nullable=True)                # 完整结果，交给前端渲染器
    error = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)


# 建表 / 幂等迁移 / 内置管理员初始化统一由 bootstrap_database() 触发，
# 不再在模块导入时执行——这样导入 ORM 模型、跑单测、执行离线脚本都不需要连库。
# 运行时由 FastAPI lifespan 和后台 Worker 启动时各调用一次。

# ========== 幂等迁移：为已存在的表补新增列（避免 ALTER TABLE 手动操作） ==========
# Phase 3A（docs/db-migration-plan.md）之后，这份列表冻结在 30 条——不再是新增字段的
# 地方。它仍然在跑，是因为全新空库跑 create_all() 产出的结构现在跟 Alembic 的新基线
# （migrations/versions/20260924_0001_trusted_baseline.py）完全一致（已验证，diff 为空），
# 本地开发 / CI 每次都是全新空库，两条路径殊途同归，暂时不用二选一。但新增字段/表
# 一律走新的 Alembic 迁移文件，不要在下面这个 migrations 列表里加新条目——
# tests/test_db_migrations.py 的 test_run_migrations_list_is_frozen 会在有人加条目时报错提醒。
def _run_migrations():
    """启动时自动执行的幂等迁移，仅列不存在时才加"""
    from sqlalchemy import text, inspect
    inspector = inspect(engine)
    migrations = [
        # (表名, 列名, DDL)
        ("agent", "memory_enabled",
         "ALTER TABLE agent ADD COLUMN memory_enabled INT DEFAULT 1 COMMENT '0=关 1=开 长期记忆'"),
        ("agent_run", "conversation_id",
         "ALTER TABLE agent_run ADD COLUMN conversation_id INT NULL, ADD CONSTRAINT fk_run_conv FOREIGN KEY (conversation_id) REFERENCES conversation(id) ON DELETE SET NULL"),
        ("knowledge", "error_msg",
         "ALTER TABLE knowledge ADD COLUMN error_msg TEXT NULL COMMENT '知识库处理失败原因'"),
        ("knowledge", "is_enabled",
         "ALTER TABLE knowledge ADD COLUMN is_enabled INT DEFAULT 1 COMMENT '0=禁用 1=启用 是否参与RAG检索'"),
        ("conversation", "is_pinned",
         "ALTER TABLE conversation ADD COLUMN is_pinned INT DEFAULT 0 COMMENT '0=普通 1=置顶'"),
        ("conversation", "is_archived",
         "ALTER TABLE conversation ADD COLUMN is_archived INT DEFAULT 0 COMMENT '0=正常 1=归档'"),
        ("user", "is_disabled",
         "ALTER TABLE `user` ADD COLUMN is_disabled INT DEFAULT 0 COMMENT '0=启用 1=禁用'"),
        ("user", "last_login_at",
         "ALTER TABLE `user` ADD COLUMN last_login_at DATETIME NULL COMMENT '最后登录时间'"),
        ("user", "last_seen_at",
         "ALTER TABLE `user` ADD COLUMN last_seen_at DATETIME NULL COMMENT '最后访问时间'"),
        ("user", "phone",
         "ALTER TABLE `user` ADD COLUMN phone VARCHAR(20) NULL COMMENT '注册手机号', ADD UNIQUE KEY uq_user_phone (phone)"),
        ("background_task", "retry_count",
         "ALTER TABLE background_task ADD COLUMN retry_count INT NOT NULL DEFAULT 0 COMMENT '重试次数'"),
        ("background_task", "parent_task_id",
         "ALTER TABLE background_task ADD COLUMN parent_task_id INT NULL, ADD CONSTRAINT fk_task_parent FOREIGN KEY (parent_task_id) REFERENCES background_task(id)"),
        ("background_task", "next_run_at",
         "ALTER TABLE background_task ADD COLUMN next_run_at DATETIME NULL COMMENT '任务最早可领取时间，用于失败后延迟重试'"),
        ("user_profile", "auto_summary",
         "ALTER TABLE user_profile ADD COLUMN auto_summary TEXT NULL COMMENT 'AI自动提炼的用户画像'"),
        ("user_profile", "last_inferred_at",
         "ALTER TABLE user_profile ADD COLUMN last_inferred_at DATETIME NULL COMMENT '最近一次自动画像更新时间'"),
        # ---- 知识库空间升级（阶段1）----
        ("knowledge", "space_id",
         "ALTER TABLE knowledge ADD COLUMN space_id INT NULL COMMENT '所属知识库空间'"),
        ("knowledge", "category",
         "ALTER TABLE knowledge ADD COLUMN category VARCHAR(60) NULL COMMENT '文档分类'"),
        ("knowledge", "tags_json",
         "ALTER TABLE knowledge ADD COLUMN tags_json TEXT NULL COMMENT '文档标签'"),
        ("knowledge", "version",
         "ALTER TABLE knowledge ADD COLUMN version VARCHAR(40) NULL COMMENT '用户自填版本号'"),
        ("knowledge", "source_type",
         "ALTER TABLE knowledge ADD COLUMN source_type VARCHAR(20) NOT NULL DEFAULT 'upload' COMMENT 'upload/web/import'"),
        ("knowledge", "source_url",
         "ALTER TABLE knowledge ADD COLUMN source_url VARCHAR(1000) NULL COMMENT 'web 抓取来源'"),
        ("knowledge", "updated_at",
         "ALTER TABLE knowledge ADD COLUMN updated_at DATETIME NULL COMMENT '最近更新时间'"),
        ("agent", "kb_top_k",
         "ALTER TABLE agent ADD COLUMN kb_top_k INT NOT NULL DEFAULT 5 COMMENT '知识库检索 top_k'"),
        ("agent", "kb_rerank_enabled",
         "ALTER TABLE agent ADD COLUMN kb_rerank_enabled INT NOT NULL DEFAULT 0 COMMENT '知识库检索是否 rerank'"),
        ("agent", "kb_force_citation",
         "ALTER TABLE agent ADD COLUMN kb_force_citation INT NOT NULL DEFAULT 1 COMMENT '回答强制带来源'"),
        ("agent", "kb_refuse_when_empty",
         "ALTER TABLE agent ADD COLUMN kb_refuse_when_empty INT NOT NULL DEFAULT 1 COMMENT '无命中时拒答'"),
        ("knowledge", "chunk_size",
         "ALTER TABLE knowledge ADD COLUMN chunk_size INT NULL COMMENT '用户自选切块大小(字符)，NULL=用默认'"),
        ("user_widgets", "last_alert_level",
         "ALTER TABLE user_widgets ADD COLUMN last_alert_level VARCHAR(10) NULL COMMENT 'ok/warn/alert，外部告警推送去重用'"),
        # ---- 认证安全：token 版本号 ----
        ("user", "auth_version",
         "ALTER TABLE `user` ADD COLUMN auth_version INT NOT NULL DEFAULT 0 COMMENT 'token 版本号，改密码/强制下线时+1'"),
        ("user", "password_changed_at",
         "ALTER TABLE `user` ADD COLUMN password_changed_at DATETIME NULL COMMENT '最近一次修改密码时间'"),
    ]
    with engine.connect() as conn:
        for table, col, ddl in migrations:
            try:
                existing_cols = {c["name"] for c in inspector.get_columns(table)}
            except Exception:
                # 表不存在，create_all 会处理，跳过
                continue
            if col not in existing_cols:
                try:
                    conn.execute(text(ddl))
                    conn.commit()
                    print(f"[Migration] 已为 {table} 添加列 {col}")
                except Exception as e:
                    print(f"[Migration] 添加列 {col} 失败: {e}")

        # 列类型 / 约束变更（幂等：仅当当前不满足目标时才 MODIFY）
        column_type_migrations = [
            # (表, 列, 期望可空?, DDL)
            ("knowledge", "agent_id", True,
             "ALTER TABLE knowledge MODIFY COLUMN agent_id INT NULL"),
        ]
        for table, col, want_nullable, ddl in column_type_migrations:
            try:
                cols = {c["name"]: c for c in inspector.get_columns(table)}
            except Exception:
                continue
            info = cols.get(col)
            if info is None:
                continue
            if bool(info.get("nullable")) == bool(want_nullable):
                continue
            try:
                conn.execute(text(ddl))
                conn.commit()
                print(f"[Migration] 已调整 {table}.{col} 可空性 -> {want_nullable}")
            except Exception as e:
                print(f"[Migration] 调整 {table}.{col} 失败: {e}")

        index_migrations = [
            ("agent", "idx_agent_user_id_id", "CREATE INDEX idx_agent_user_id_id ON agent (user_id, id)"),
            ("llm_config", "idx_llm_config_user_model", "CREATE INDEX idx_llm_config_user_model ON llm_config (user_id, model_name)"),
            ("llm_config", "idx_llm_config_user_active", "CREATE INDEX idx_llm_config_user_active ON llm_config (user_id, is_active)"),
            ("chat", "idx_chat_user_agent_time", "CREATE INDEX idx_chat_user_agent_time ON chat (user_id, agent_id, create_time)"),
            ("knowledge", "idx_knowledge_user_created", "CREATE INDEX idx_knowledge_user_created ON knowledge (user_id, created_at)"),
            ("knowledge", "idx_knowledge_agent_created", "CREATE INDEX idx_knowledge_agent_created ON knowledge (agent_id, created_at)"),
            ("knowledge", "idx_knowledge_agent_enabled_status", "CREATE INDEX idx_knowledge_agent_enabled_status ON knowledge (agent_id, is_enabled, status)"),
            ("knowledge_chunk", "idx_knowledge_chunk_knowledge_index", "CREATE INDEX idx_knowledge_chunk_knowledge_index ON knowledge_chunk (knowledge_id, chunk_index)"),
            ("knowledge_chunk", "idx_knowledge_chunk_vector_id", "CREATE INDEX idx_knowledge_chunk_vector_id ON knowledge_chunk (vector_id)"),
            ("agent_run", "idx_agent_run_user_started", "CREATE INDEX idx_agent_run_user_started ON agent_run (user_id, started_at)"),
            ("agent_run", "idx_agent_run_agent_started", "CREATE INDEX idx_agent_run_agent_started ON agent_run (agent_id, started_at)"),
            ("agent_run", "idx_agent_run_agent_conversation_started", "CREATE INDEX idx_agent_run_agent_conversation_started ON agent_run (agent_id, conversation_id, started_at)"),
            ("agent_run", "idx_agent_run_status_started", "CREATE INDEX idx_agent_run_status_started ON agent_run (status, started_at)"),
            ("agent_step", "idx_agent_step_run_step", "CREATE INDEX idx_agent_step_run_step ON agent_step (run_id, step_no)"),
            ("background_task", "idx_background_task_user_status_created", "CREATE INDEX idx_background_task_user_status_created ON background_task (user_id, status, created_at)"),
            ("background_task", "idx_background_task_user_type_created", "CREATE INDEX idx_background_task_user_type_created ON background_task (user_id, task_type, created_at)"),
            ("background_task", "idx_background_task_status_type_created", "CREATE INDEX idx_background_task_status_type_created ON background_task (status, task_type, created_at, id)"),
            ("background_task", "idx_background_task_status_type_next_run", "CREATE INDEX idx_background_task_status_type_next_run ON background_task (status, task_type, next_run_at, created_at, id)"),
            ("background_task", "idx_background_task_status_started", "CREATE INDEX idx_background_task_status_started ON background_task (status, started_at)"),
            ("operation_log", "idx_operation_log_created", "CREATE INDEX idx_operation_log_created ON operation_log (created_at)"),
            ("operation_log", "idx_operation_log_user_created", "CREATE INDEX idx_operation_log_user_created ON operation_log (user_id, created_at)"),
            ("operation_log", "idx_operation_log_method_created", "CREATE INDEX idx_operation_log_method_created ON operation_log (method, created_at)"),
            ("operation_log", "idx_operation_log_status_created", "CREATE INDEX idx_operation_log_status_created ON operation_log (status_code, created_at)"),
            ("operation_log", "idx_operation_log_latency_created", "CREATE INDEX idx_operation_log_latency_created ON operation_log (latency_ms, created_at)"),
            ("skill", "idx_skill_user_id", "CREATE INDEX idx_skill_user_id ON skill (user_id)"),
            ("skill", "idx_skill_public", "CREATE INDEX idx_skill_public ON skill (is_public)"),
            ("memory", "idx_memory_user_agent_type_created", "CREATE INDEX idx_memory_user_agent_type_created ON memory (user_id, agent_id, memory_type, created_at)"),
            ("memory", "idx_memory_agent_id", "CREATE INDEX idx_memory_agent_id ON memory (agent_id)"),
            ("user_profile", "idx_user_profile_user_id", "CREATE INDEX idx_user_profile_user_id ON user_profile (user_id)"),
            ("user_workspace", "idx_user_workspace_user_id", "CREATE INDEX idx_user_workspace_user_id ON user_workspace (user_id)"),
            ("web_monitor", "idx_web_monitor_user_active", "CREATE INDEX idx_web_monitor_user_active ON web_monitor (user_id, is_active)"),
            ("web_monitor", "idx_web_monitor_user_checked", "CREATE INDEX idx_web_monitor_user_checked ON web_monitor (user_id, last_checked_at)"),
            ("conversation", "idx_conversation_user_agent_flags_time", "CREATE INDEX idx_conversation_user_agent_flags_time ON conversation (user_id, agent_id, is_archived, is_pinned, update_time)"),
            ("conversation", "idx_conversation_user_time", "CREATE INDEX idx_conversation_user_time ON conversation (user_id, update_time)"),
            ("message", "idx_message_conversation_time", "CREATE INDEX idx_message_conversation_time ON message (conversation_id, create_time)"),
        ]
        for table, index_name, ddl in index_migrations:
            try:
                existing_indexes = {idx["name"] for idx in inspector.get_indexes(table)}
            except Exception:
                continue
            if index_name not in existing_indexes:
                try:
                    conn.execute(text(ddl))
                    conn.commit()
                    print(f"[Migration] 已为 {table} 添加索引 {index_name}")
                except Exception as e:
                    print(f"[Migration] 添加索引 {index_name} 失败: {e}")
# ========== 迁移结束 ==========
# 创建Session
SessionLocal = sessionmaker(bind=engine)


def _ensure_builtin_admin():
    """确保内置管理员账号存在，便于本地部署后直接进入后台。

    账号名/密码优先从环境变量 ADMIN_USERNAME / ADMIN_PASSWORD 读取。
    未配置密码时，默认用户名 admin，密码随机生成并打印一次，
    要求登录后立即修改，日志中不会再次出现。
    已存在的管理员账号不会在每次启动时被静默重置密码——
    只有显式设置 ADMIN_PASSWORD_RESET=true 并提供 ADMIN_PASSWORD 时，
    才会用它覆盖已有密码，用于找回丢失的管理员密码。
    """
    import bcrypt
    import secrets as _secrets

    admin_name = (os.getenv("ADMIN_USERNAME", "admin") or "admin").strip() or "admin"
    admin_password = (os.getenv("ADMIN_PASSWORD", "") or "").strip()
    force_reset = _env_bool("ADMIN_PASSWORD_RESET", False)

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.role_name == "admin").first()
        if not role:
            role = Role(role_name="admin", description="系统管理员")
            db.add(role)
            db.flush()

        user = db.query(User).filter(User.name == admin_name).first()

        if not user:
            # 首次创建：未配置 ADMIN_PASSWORD 时生成随机密码，仅打印这一次。
            if not admin_password:
                admin_password = _secrets.token_urlsafe(12)
                print(
                    f"[Seed] 未配置 ADMIN_PASSWORD，已为管理员账号 {admin_name} "
                    f"生成随机初始密码：{admin_password}（请立即登录后台并修改，"
                    "该密码不会再次打印）"
                )
            hashed = bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            user = User(name=admin_name, password=hashed, age=18)
            db.add(user)
            db.flush()
        else:
            # 账号已存在：默认不覆盖密码，避免把后台已改过的密码每次启动重置掉。
            # 仅当显式设置 ADMIN_PASSWORD_RESET=true 且提供了 ADMIN_PASSWORD 时才允许找回式重置。
            if force_reset and admin_password:
                user.password = bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                print(f"[Seed] 已按 ADMIN_PASSWORD_RESET 重置管理员 {admin_name} 的密码")
            user.is_disabled = 0
            if user.age is None:
                user.age = 18

        if role not in (user.roles or []):
            user.roles.append(role)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Seed] 内置管理员初始化失败: {e}")
    finally:
        db.close()


def _ensure_default_plan():
    """确保存在一个默认套餐，未订阅用户回退到它——避免"没有任何套餐配置"时

    配额检查逻辑无所适从。默认套餐初始不限量（monthly_token_limit=0），
    管理员可以在后台随时改成有限额的套餐，或新建套餐后把它设为默认。
    """
    db = SessionLocal()
    try:
        exists = db.query(Plan).filter(Plan.is_default == 1).first()
        if exists:
            return
        plan = db.query(Plan).filter(Plan.name == "free").first()
        if not plan:
            plan = Plan(
                name="free", display_name="免费版", monthly_token_limit=0,
                price_desc="默认套餐", is_default=1, is_enabled=1,
            )
            db.add(plan)
        else:
            plan.is_default = 1
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Seed] 默认套餐初始化失败: {e}")
    finally:
        db.close()


_BOOTSTRAP_DONE = False
_BOOTSTRAP_LOCK_NAME = "kb_bootstrap"


def _create_all_with_retry(attempts: int = 5, delay: float = 3.0) -> None:
    """建表；撞上并发 DDL / 元数据锁时退避重试（多进程冷启动的兜底）。"""
    import time
    from sqlalchemy.exc import OperationalError

    for i in range(attempts):
        try:
            Base.metadata.create_all(engine)
            return
        except OperationalError as exc:
            msg = str(getattr(exc, "orig", exc))
            transient = "1684" in msg or "concurrent DDL" in msg or "metadata lock" in msg
            if transient and i < attempts - 1:
                print(f"[Bootstrap] 建表撞并发 DDL，{delay}s 后重试（{i + 1}/{attempts}）")
                time.sleep(delay)
                continue
            raise


def _run_bootstrap_steps(seed_admin: bool) -> None:
    _create_all_with_retry()
    _run_migrations()
    if seed_admin:
        _ensure_builtin_admin()
        _ensure_default_plan()


def bootstrap_database(*, seed_admin: bool = True, force: bool = False) -> None:
    """建表 + 幂等迁移 + 内置管理员初始化。

    运行时的唯一入口：由 FastAPI lifespan、后台 Worker 启动、以及
    `python -m models.init_db` 调用。进程内只会真正执行一次。

    可用环境变量 DB_AUTO_BOOTSTRAP=0 关闭（改由 Alembic 管理表结构的部署），
    此时仍可传 force=True 强制执行。

    多进程 / `uvicorn --reload` 冷启动时可能有多个进程同时到这里，各自跑
    create_all()。并发 DDL 会触发 MySQL 元数据锁死（错误 1684 "concurrent DDL
    statement"），进而卡住所有请求。这里用 MySQL 命名锁 GET_LOCK 把建表串行化：
    拿到锁的进程建表，其它进程等待，等到后再跑一遍（create_all 幂等，是快速空操作）。
    锁按连接持有，用完即释放/断开。
    """
    global _BOOTSTRAP_DONE
    if _BOOTSTRAP_DONE:
        return
    if not force and not _env_bool("DB_AUTO_BOOTSTRAP", True):
        print("[Bootstrap] DB_AUTO_BOOTSTRAP=0，跳过自动建表/迁移")
        _BOOTSTRAP_DONE = True
        return

    from sqlalchemy import text

    lock_timeout = _env_int("DB_BOOTSTRAP_LOCK_TIMEOUT", 120)
    lock_conn = None
    have_lock = False
    try:
        lock_conn = engine.connect()
        got = lock_conn.execute(
            text("SELECT GET_LOCK(:name, :timeout)"),
            {"name": _BOOTSTRAP_LOCK_NAME, "timeout": lock_timeout},
        ).scalar()
        have_lock = got == 1
        if not have_lock:
            print(f"[Bootstrap] 未拿到建表锁（GET_LOCK 返回 {got!r}），降级为直接建表")
    except Exception as exc:  # noqa: BLE001 - 锁不可用时不阻断启动
        print(f"[Bootstrap] 建表锁不可用，降级为直接建表: {exc}")

    try:
        _run_bootstrap_steps(seed_admin)
    finally:
        if lock_conn is not None:
            try:
                if have_lock:
                    lock_conn.execute(
                        text("SELECT RELEASE_LOCK(:name)"), {"name": _BOOTSTRAP_LOCK_NAME}
                    )
            except Exception:
                pass
            lock_conn.close()

    _BOOTSTRAP_DONE = True


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


if __name__ == "__main__":
    # 允许离线执行：python -m models.init_db
    bootstrap_database(force=True)
    print("[Bootstrap] 建表与迁移完成")
