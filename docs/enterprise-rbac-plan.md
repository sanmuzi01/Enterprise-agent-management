# 企业化数据模型与授权层设计稿（Phase 3B / 3C 评审用）

状态：**设计已定稿，尚未动表**。Phase 3A（Alembic 权威化）已经完成（见
[docs/db-migration-plan.md](db-migration-plan.md) 第 5 节），这份文档的表结构设计也
定稿了——下一步是真的写迁移文件和授权层代码，还没开始动手。

## 0. 现状：不是从零开始（本节 2026-09-24 二次核对后更正过一次）

核对了一遍现有代码，企业化改造要用到的几块地基已经预留了，设计时要接上，不要重建：

| 预留位置 | 现状 |
|---|---|
| `Organization` / `Team`（[models/init_db.py:367](../models/init_db.py:367)/[376](../models/init_db.py:376)，表名 `organizations`/`teams`） | **已经是真实存在的表**，本文档最初的草稿漏看了这两个类，一度设计了同概念的新表——已改成扩展这两张表，不新建，见 1.2 节 |
| `KnowledgeSpace.team_id` / `KnowledgeSpace.organization_id`（[models/init_db.py:258](../models/init_db.py:258)） | 字段已经在，注释写"阶段6预留"，一直是 `nullable`、未使用 |
| `SpaceMember`（[models/init_db.py:303](../models/init_db.py:303)） | 空间级三档角色 admin/editor/viewer，已在用，语义就是本文档"三层不合并"里的第三层 |
| `KbAuditLog`（[models/init_db.py:322](../models/init_db.py:322)） | 知识库空间/文档/成员/绑定的写操作审计表，已建但看起来还没接线——企业化的审计需求可以直接扩展这张表，不用新建 |
| `Role` + `is_admin_user()`（[service/admin_service.py:37](../service/admin_service.py:37)） | 目前是"平台全局管理员"判断：角色表命中 `ADMIN_ROLE_NAMES` 或用户名落在 `ADMIN_USER_NAMES`。这条要继续保留，但只管平台超管，企业内部的权限判断迁到新的授权层（见第 2 节） |

### 0.1 核对时顺手发现的更大问题：Alembic 目前完全没有真的跑过

用只读查询核对本地开发数据库后确认：

- `alembic_version` 表**不存在**——8 个迁移文件从写下来到现在，从没有被 `alembic upgrade`
  真正执行过一次。当前数据库的实际结构 100% 是 `models/init_db.py` 的
  `Base.metadata.create_all()` + `_run_migrations()`（约 24 条手写幂等 `ALTER TABLE`）
  拼出来的，Alembic 文件只是摆在那里的历史记录，不是真正在起作用的那一套。
- 恰好因为这样，`migrations/versions/20260911_0006_space_permissions.py` 里的
  `op.create_table("organizations", ...)`/`op.create_table("teams", ...)` 从未被执行，
  但对应的 `Organization`/`Team` **ORM 类是真实存在的**，两张表已经通过 `create_all()`
  建好了（本地库确认：都在，都是 0 行）。也就是说现在如果第一次真的对着这个数据库跑
  `alembic upgrade head`，会在这条 `op.create_table` 撞上"表已存在"直接失败——`user_profile`
  等表也是一样的情况，第一个真正会失败的大概是 0002。
- 这就是 Phase 3A 要解决的真实问题，不是走个形式："在全新空库验证 alembic upgrade head
  能够完整建库"这句话现在还做不到，因为 `migrations/env.py` 里 `target_metadata = None`
  （代码注释写着"暂不导入业务模型，避免触发旧 create_all"），`alembic revision
  --autogenerate` 目前根本没法用——这是 Phase 3A 要先修的第一个东西，细节见
  [docs/db-migration-plan.md](db-migration-plan.md)（Phase 3A 单独的执行记录，跟这份
  企业模型设计稿分开，避免两件事混在一份文档里）。

## 1. 数据模型

### 1.1 命名：不新增 Department，也不新建 Organization/Team

```
Organization = 企业（表名 organizations，已存在，单企业部署下长期只会有 1 行）
Team         = 部门（表名 teams，已存在，前端显示"部门"；不出现 department_id）
```

### 1.2 扩展现有表 + 三张新表

三个开放问题已定：**单企业多部门**（复用已存在的 `organizations`，长期只有 1 行；
`teams` 才是真正多行的单位）、角色**新建外键表**（不用字符串枚举）、部门管理员**能看到**
部门下所有知识库空间（见第 2 节——落地时改成了扩展 `service/access_control.py`，
不是本节最初设计的 `get_accessible_space_ids`）。

`organizations`/`teams` 已经存在（[models/init_db.py:367](../models/init_db.py:367)），
现有列是 `id/name/owner_user_id/created_at`（teams 多一个 `organization_id`）——
`owner_user_id` 是隐式创建者，跟 `KnowledgeSpace.user_id` 是 owner、`SpaceMember`
才是显式成员表的既有模式完全一致，不用动；只额外加一个 `status` 列（软停用用，
现有表没有）。**不新建同名概念的表**，新建的只有下面三张：

角色新建一张独立的 `enterprise_role` 表，不是塞进现有的 `Role`
（[models/init_db.py:157](../models/init_db.py:157)）——那张表是平台级角色（决定
`is_admin_user()`），语义和生命周期都不一样，混在一起以后没法单独改企业角色目录。

```python
# --- 对现有两张表的追加（ALTER TABLE ADD COLUMN，纯新增，不改已有列） ---
# organizations.status = Column(String(20), nullable=False, default="active")  # active/disabled
# teams.status         = Column(String(20), nullable=False, default="active")

class EnterpriseRole(Base):
    """企业/部门角色目录。scope 区分用在哪一层，同一层内 code 唯一。

    初始数据（迁移里插入，不是代码里硬编码判断）：
      scope=organization: owner(等级3) / admin(2) / auditor(1) / member(0)
      scope=team:         admin(2) / editor(1) / member(0)
    `rank` 用于"至少要有 X 级"的判断（require_org_role("admin") 实际比较 rank），
    不用在代码里列举所有可能的角色名。
    """
    __tablename__ = "enterprise_role"
    __table_args__ = (
        Index("uq_enterprise_role_scope_code", "scope", "code", unique=True),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(20), nullable=False)   # organization / team
    code = Column(String(30), nullable=False)    # owner / admin / auditor / editor / member
    name = Column(String(60), nullable=False)    # 显示名，如"企业管理员"
    rank = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class OrganizationMember(Base):
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
    __tablename__ = "team_members"
    __table_args__ = (
        Index("uq_team_member", "team_id", "user_id", unique=True),
        Index("idx_team_member_user", "user_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("teams.id", name="fk_tm_team"), nullable=False)
    user_id = Column(Integer, ForeignKey("user.id", name="fk_tm_user"), nullable=False)
    role_id = Column(Integer, ForeignKey("enterprise_role.id", name="fk_tm_role"), nullable=False)
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
```

（表名用的是已存在的 `organizations`/`teams`，不是 `organization`/`team`——这是 1.1 节
更正之后的实际外键目标，跟前面小节保持一致。）

`role_id` 指向哪个 `scope` 的 `enterprise_role` 由写入时的 service 层校验
（`organization_members.role_id` 必须是 `scope="organization"` 的行），不指望数据库
跨表 CHECK 约束覆盖——MySQL 8（本项目用的版本）虽然支持 CHECK，但不能引用别的表，
这类跨表条件历来都是应用层校验，跟 1.4 节"Team 必须属于同一个 Organization"的校验
放在一起做。

`status` 字段（而不是直接删行）是为了停用成员时保留审计痕迹，跟 `KnowledgeSpace.status`
（active/archived）的既有风格一致。

### 1.3 三层权限语义不同，不合并

```
OrganizationMember.role  → 能不能管理"企业"这个范围（成员、企业级模型配置、企业审计）
TeamMember.role          → 能不能管理"部门"这个范围（部门知识库、部门助手、部门成员）
SpaceMember.role         → 能不能访问"某一个具体知识库空间"（现有表，继续保留原样）
```

不合并的理由：一个部门管理员不代表能访问该部门下所有知识库空间（空间可能标了
`visibility=private` 只给创建者+被邀请的人），一个知识库空间的 `editor` 也不代表
对这个部门有任何管理权。三层各自独立判断，缺一层就在那一层拒绝。

### 1.4 现有表补外键

```
teams.organization_id           → 已存在字段（当前是普通 Integer，没有真正的外键约束），
                                   补 ForeignKey("organizations.id")
KnowledgeSpace.organization_id  → 已存在字段，改为在应用层强制非空写入（企业化上线后）
KnowledgeSpace.team_id          → 已存在字段，同上，补 ForeignKey("teams.id")
```

写入时的强制校验：`KnowledgeSpace.team_id` 对应的 `Team.organization_id` 必须等于
`KnowledgeSpace.organization_id`——不允许把一个空间挂到"别的企业的部门"下面。这条校验
放进 service 层（`knowledge_space_service` 的创建/转移入口），不指望数据库约束覆盖跨表条件。

### 1.5 迁移顺序（依赖 Phase 3A 先把 Alembic 权威化做完）

1. `organizations`/`teams` 加 `status` 列（ADD COLUMN，纯新增）；补 `teams.organization_id`
   和 `KnowledgeSpace.team_id`/`organization_id` 的真实外键约束（现有数据都是
   `NULL`/未使用，加约束不会因为脏数据失败——上线前会再跑一次校验确认）。
2. 建 `enterprise_role` / `organization_members` / `team_members` 三张新表，纯新增。
3. 数据回填：为当前部署把 `organizations` 表插入 1 行（"默认企业"，`owner_user_id` 填平台
   管理员），把所有现有用户批量插入 `organization_members`（role=member，平台管理员
   role=owner）——这一步是数据迁移脚本，不是 schema 迁移，要单独写、要能重复执行不出错（幂等）。
4. `KnowledgeSpace.organization_id` 批量回填成默认企业 id（现有数据全部挂到默认企业下，不建
   `Team`、`team_id` 留空——没有部门信息，不能瞎猜）。
5. 上面四步全部落地、跑过一遍全新空库 `alembic upgrade head` 验证后，才开始第 3 节的路由改造。

## 2. 统一授权层

**执行时更正过一次**：这一节最初设计了 `require_org_role`/`require_team_role`/
`require_space_permission`/`get_accessible_space_ids` 四个函数，全部放进新建的
`service/enterprise_access.py`。落地时发现后两个是重复造轮子——知识库空间的可见性
判断早就有一份现成的、被 `service/knowledge_space/*` 全线在用的实现：
`service/access_control.py` 的 `get_owned_space[_async]`/`get_space_role[_async]`/
`user_space_ids[_async]`（本来就是"阶段6预留"要接团队/组织可见性的地方）。两套并存
迟早会算出不一样的结果，所以改成**扩展这份已有实现**，不新写一份：

- `service/knowledge_space/membership.py` 的 `resolve_role()` 加一个
  `is_team_admin` 参数——部门 team admin 视同这个空间的 `admin`，即使不是这个空间的
  `SpaceMember`。
- `service/access_control.py` 的三对函数（同步+异步）在原有"owner / SpaceMember"
  两个来源之外接入第三个来源，调用点（`space_async_service.py`/`document_service.py`
  等）完全不用改。
- 新增 `models/enterprise_dao.py` 提供 `is_team_admin_of_team[_async]`/
  `list_space_ids_where_team_admin[_async]` 两个只读查询，`access_control.py` 是唯一
  调用方。

`service/enterprise_access.py` 最终只留下两个真正新增的函数（组织/部门维度的判断，
之前没有任何模块做过，没有旧实现可扩展）：

```python
def require_org_role(*roles: str):
    """FastAPI 依赖工厂：当前用户必须是这个企业的成员，且角色等级 >= roles 里最低要求的那个，否则 403。"""

def require_team_role(*roles: str):
    """同上，范围换成部门；如果部门不属于当前用户所在企业，视为不存在（404，不是 403——
    不暴露"这个部门存在但你无权"这条信息）。"""
```

### 2.1 校验顺序（固定，不因路由而变）

```
1. 是否属于该企业（organization_members 有效行）
2. 是否具有所需的组织/部门角色
3. 是否拥有该具体资源的访问权限（SpaceMember 或资源自身的 owner 字段）
4. 是否允许当前这个操作（比如 viewer 不能删除文档）
```

第 1、2 步不通过统一走 404（不暴露资源/部门存在性）；第 3、4 步不通过走 403
（已经确认资源存在，只是没权限，这条信息本身不敏感）。这跟现有
`test_cross_user_access_is_not_found_not_forbidden` 类测试的既有约定一致，改造时延续，不新发明一套。

### 2.2 与 `is_admin_user()` 的关系

`is_admin_user()`（[service/admin_service.py:37](../service/admin_service.py:37)）**只保留给平台超级管理员**
（运维这套系统本身的人，理论上单企业部署下这个角色几乎不出现在业务操作里）。
企业内部的所有权限判断——包括原来很多路由里"没有专门权限模型，索性判断
`is_admin_user()` 顶替"的地方——迁移到 `require_org_role`/`require_team_role`。
这是本次改造里最容易漏改的一步，第 3 节按模块清点。

## 3. 逐模块改造范围清单

**说明**：下面的路由数是对各 `FasdtApi/*.py` 文件按 `@router.get/post/put/patch/delete`
计数得到的规模参考，用来定改造顺序和工作量，**不是逐条读过之后的审阅结论**——具体每个
路由要不要加、加哪一层校验，要在改那个模块时单独过一遍，不能照这张表机械套用。

| 顺序 | 模块 | 涉及文件 | 路由数（规模参考） | 结论 |
|---|---|---|---|---|
| 1 | 知识库和文件下载 | `knowledge.py`、`knowledge_space.py`、`attachment_route.py` | 14 + 17 + 3 = 34 | **已完成**，见第 7 节 |
| 2 | Agent 及其知识库绑定 | `agent.py`、`agent_pipeline.py`、`agent_run.py` | 18 + 5 + 2 = 25 | **已确认不需要改代码**，见第 8 节——`agent_service._validate_space_ids` 早就调用 `access_control.user_space_ids`，模块 1 改完自动生效，补了一条真实路由测试验证 |
| 3 | Skill | `skill_route.py` | 25 | **暂不动**：创建/编辑/删除已经是平台管理员专属（见 [docs/testing.md](testing.md) Step 0 章节）。"官方 Skill 按部门发布"是全新能力，不是收紧现有权限，属于 Phase 3D |
| 4 | 模型配置 | `llm_config.py` | 6 | **确认不需要改**：现在是纯个人配置（`current_user` 自己的 Key），每个路由天然只碰自己的数据，没有"该不该挡住别人"这个问题；"企业统一模型配置"是全新的共享资源模型，属于 Phase 3D，到时候再加权限判断才有意义 |
| 5 | 外部连接器 | `agent.py` 里的 `api-connectors` 路由 | 4 | **暂不动**：Step 0 已经做了管理员/开关粗粒度收紧（见 [docs/testing.md](testing.md)），部门维度是 Phase 3D 的事 |
| 6 | 后台任务、统计与审计 | `background_task.py`、`evaluation.py`、`rag_debug.py`、`admin.py` 里的统计/审计部分 | 5 + 8 + 6 + 21 | **确认不需要改**：全是个人数据自查或已有管理员判断；`evaluation.py`/`rag_debug.py` 涉及 `space_id` 的路由已经通过模块 1 的 `access_control.py` 间接生效。组织/部门级审计要等 Phase 3D 有了真正的组织管理路由（建部门、加成员这些操作）才有东西可审计，`KbAuditLog` 到时候直接扩展，现在没有对象 |

## 4. 决策记录

| 问题 | 决策 |
|---|---|
| 要不要做"多企业"数据模型？ | **单企业多部门**：保留 `Organization` 表（结构可扩展），但这次部署下长期只有 1 行；`Team` 是真正的多行单位。`require_org_role` 的"是否属于该企业"这一步照做，不跳过 |
| `role` 用字符串还是外键表？ | **新建**：新增 `enterprise_role` 目录表，`organization_members`/`team_members` 用 `role_id` 外键，不用字符串枚举（见 1.2 节） |
| 部门管理员能不能看到部门下所有空间？ | **能看到**：实现为 `service/access_control.py` 的第三个来源"该空间所属 Team 的 team admin"，即使不是具体空间的 `SpaceMember`（见第 2、7 节） |

## 5. 下一步

设计、三个开放问题、Phase 3A 这个前置依赖都已经落地。剩下：
1. 把 1.2 节的表结构落成 Alembic 迁移文件 + 1.5 节的数据回填脚本（含幂等性测试）。
2. 授权层（第 2 节）先落地并测试，再按第 3 节的顺序逐模块接入、每接入一个模块跑一遍
   那个模块的路由级测试确认没有意外放宽或收紧权限。

这两步还没开始——Phase 3A 完成只是解除了阻塞，不代表自动接着做，等你确认再动手。

## 6. 执行结果（第 1 步已落地，2026-09-24）

第 1 步（表结构 + 数据回填）做完了，第 2 步（统一授权层）还没开始。

- `models/init_db.py`：`Organization`/`Team` 加 `status` 列，`teams.organization_id`
  补真实外键；新增 `EnterpriseRole`/`OrganizationMember`/`TeamMember` 三个类；
  `KnowledgeSpace.team_id`/`organization_id` 补真实外键（原来只是普通 `Integer`，
  没有约束）。
- `migrations/versions/20260924_0002_enterprise_rbac_tables.py`：对着独立的空库
  用 autogenerate 生成、验证过跟 Phase 3A 一样的"空库 upgrade head → 再 autogenerate
  → diff 为空"流程，已经应用到本地开发库。`enterprise_role` 的 7 行初始数据
  （organization: owner/admin/auditor/member；team: admin/editor/member）在迁移里
  用 `op.bulk_insert` 种好，不是代码里硬编码判断。
- `scripts/backfill_default_organization.py`：一次性数据回填脚本（`--dry-run`/`--yes`），
  已经在本地开发库跑过：建了 1 个"默认企业"（`owner_user_id` 是当前的平台管理员），
  9 个现有用户全部批号进 `organization_members`（管理员 role=owner，其余 role=member），
  1 个知识库空间的 `organization_id` 回填成默认企业。跑了两遍确认幂等（第二遍 0 新增）。
- `tests/test_backfill_default_organization.py`：真实 DB 集成测试，新建两个测试用户
  验证会被正确回填成 member、重复跑不会插出第二条。
- `tests/_route_client.py` 的 `_purge_users`（路由测试和 E2E 冒烟测试共用的清理函数）
  补了 `organization_members`/`team_members` 两条 DELETE——这两张新表有 `user.id` 外键，
  不先清它们，删测试用户会直接撞 FK 报错。

689 个测试全绿。

**第 2 步也做完了**（`service/enterprise_access.py`）：落地时发现最初设计的
`require_space_permission`/`get_accessible_space_ids` 跟已有的 `service/access_control.py`
重复，改成扩展后者，详情和理由见第 2 节开头的更正说明。最终 `enterprise_access.py`
只留 `require_org_role`/`require_team_role`（真正新增的组织/部门维度判断），
校验顺序和 404/403 语义跟第 2 节设计一致；按"等级 >= 最低要求"判断，不是精确匹配
角色代码——要求 `"admin"` 时 `"owner"` 也能过，不用每个路由都把上级角色抄一遍。
`tests/test_enterprise_access.py`（组织/部门）+ `tests/test_access_control_team_admin.py`
（知识库空间，同步+异步各测一遍）覆盖：非成员 404、等级不够 403、更高等级放行、
跨企业的部门视为不存在、owner/SpaceMember 两条旧来源没被新加的第三条来源挤掉。

## 7. 执行结果（模块 1：知识库和文件下载，2026-09-24）

第 3 节改造清单的模块 1（`knowledge.py`/`knowledge_space.py`/`attachment_route.py`）
做完了，路由文件本身**一行没改**——这三个文件的权限判断早就全部委托给
`service/access_control.py`（`get_owned_space[_async]` 等），不是在路由层各自手写的。
所以"接入"落在 `service/access_control.py` 自己身上（见第 2 节的更正说明），改完
所有调用点自动生效：

- `service/knowledge_space/membership.py`：`resolve_role()` 加 `is_team_admin` 参数。
- `service/access_control.py`：`get_owned_space`/`get_space_role`/`user_space_ids`
  三对函数（同步+异步）都接入"部门 team admin 也能看"这第三个来源。
- `models/enterprise_dao.py`（新增）：`is_team_admin_of_team[_async]`/
  `list_space_ids_where_team_admin[_async]`，`access_control.py` 是唯一调用方。
- `tests/test_access_control_team_admin.py`：6 个真实 DB 测试，owner/SpaceMember 两条
  旧来源 + team admin 新来源，同步异步各测一遍；部门普通成员（非 admin）确认不会
  因为同部门就拿到空间权限。

另外两个文件核实过之后确认不需要改，原因跟最初设计稿预想的不一样（设计稿写的时候
没有真的读过这两个文件，核对后更正）：

- `knowledge.py` 是**另一套更老的系统**——"Agent 私有知识库"（`Knowledge`/`agent_id`
  维度，[service/access_control.py](../service/access_control.py) 的 `get_owned_knowledge`），
  跟 `knowledge_spaces` 是两个不同的模型，从来只有 `user_id` 单人归属，没有空间/成员/
  部门这层概念，团队可见性天然不适用。
- `attachment_route.py` 是**聊天附件**（喂给 Skill 脚本用），跟知识库空间完全无关，
  权限判断就是 `attachment_service.resolve(current_user.id, att_id)` 这种单人归属，
  跟本次改造的对象不是一回事。

702 个测试全绿。

## 8. 执行结果（模块 2-6 + 一个顺手挖出的真实缺口，2026-09-24）

### 模块 2：Agent 及其知识库绑定 —— 确认不需要改代码

`service/agent_service.py` 的 `_validate_space_ids()` 早就调用
`service.access_control.user_space_ids`（校验要绑定的 `space_ids` 是不是当前用户能
访问的空间）——这正是模块 1 改过的那个函数。也就是说模块 1 一改完，"部门管理员能把
本部门的知识库空间绑给自己的 Agent"这条需求已经自动满足，不用再碰 `agent.py`/
`agent_pipeline.py`/`agent_run.py` 一行代码。

`tests/test_routes_isolation.py` 新增
`test_agent_space_binding_allows_department_team_admin`：真实路由级测试，alice 是
某部门的 team admin（不是那个空间的 SpaceMember），直接建 Agent 时绑 bob 建在这个部门下
的知识库空间，验证走的通——不是只信"代码逻辑上应该行"，是真的跑一遍 HTTP 请求确认。

### 模块 3-6：核实后确认现在都不需要改

逐个读了 `skill_route.py`、`llm_config.py`、`agent.py` 的连接器路由、
`background_task.py`/`evaluation.py`/`rag_debug.py` 之后的结论：这几个模块要么已经
被模块 1 间接覆盖（`evaluation.py`/`rag_debug.py` 里带 `space_id` 的路由），要么本来
就是纯个人数据、没有"该不该挡住别人"这个问题（`llm_config.py`、后台任务自查），要么
真正要做的是全新的业务能力而不是收紧现有权限（Skill 按部门发布、企业统一模型配置、
组织管理审计）——这些全部是 Phase 3D 的范围，不是"把已有权限改成企业感知"这件事
能顺带做的。结论记录见第 3 节表格，不重复展开。

### 顺手挖出的真实缺口：新用户注册没有自动入会

核对模块 4 时想到一个问题："如果给 `llm_config.py` 之类的路由加 `require_org_role`，
新注册的用户会不会因为不在 `organization_members` 里而被 404 挡住？"——查了一下
`service/auth_service.py`/`auth_async_service.py` 的 `register()`，确认答案是**会**：
`scripts/backfill_default_organization.py` 只覆盖了回填时已经存在的用户，注册流程
从来没有让新用户加入默认企业这一步。如果不修，这个缺口现在还不影响任何路由（因为
最终确认模块 3-6 都不需要挂 `require_org_role`），但只要以后任何一个路由挂上
`require_org_role`，所有新注册用户都会被无声地挡在外面——这类"当下不触发、未来一定
爆雷"的缺口，找到了不该留着，已经修：

- `models/enterprise_dao.py` 新增 `enroll_in_default_organization[_async]`：注册成功
  后自动把新用户加进默认企业（role=member）。找不到默认企业（全新部署、CI、大部分
  测试库都是这样——还没跑过回填脚本）就静默跳过，不阻断注册；出错也独立
  `rollback()`，不拖累注册本身的事务。
- `service/auth_service.py`/`auth_async_service.py` 的 `register()`：`create_user()`
  成功后调用它。
- `scripts/backfill_default_organization.py` 的 `DEFAULT_ORG_NAME` 常量挪到
  `models/enterprise_dao.py`，脚本改成从那里导入——两处各写一份名字迟早会对不上。
- `tests/test_auth_default_org_enrollment.py`：6 个真实 DB 测试，同步/异步注册各测
  一遍"注册成功后真的进了 organization_members"，加上 `enroll_in_default_organization`
  本身的两个边界（默认企业不存在时静默跳过、重复调用不报错）。

707 个测试全绿。至此，设计稿第 3 节的逐模块改造清单**全部有了结论**（完成或确认
不需要改），Phase 3B 的路由接入工作告一段落。剩下的组织/部门管理相关的真正业务功能
（建部门、加成员、企业统一模型配置、Skill 按部门发布等）都属于 Phase 3D，需要新的
HTTP 路由，不是这次"给已有权限接上企业感知"能覆盖的范围。

## 9. Phase 3D 架构决策：零信任方案的取舍（2026-09-27）

用户带来一份完整的"零信任企业架构"方案（中央控制平面 + 部门数据平面 + PEP/PDP +
不可抵赖审计），讨论后达成一致：**方向对，但按多租户 SaaS 的威胁模型设计，超出
"单企业私有化部署、单企业多部门"这个实际形态**。以下是收敛后的结论，作为 Phase 3D
的架构基线。

### 9.1 明确不做（不是"以后再做"，是当前规模下不需要）

| 方案里的项 | 为什么不做 |
|---|---|
| 独立 PDP/PEP 微服务 | NIST SP 800-207 本身不要求 PDP/PEP 是独立服务，"逻辑组件"即可满足；已有 `service/enterprise_access.py` + `service/access_control.py` 就是逻辑 PDP，FastAPI 依赖/Service 入口/RAG 入口/Tool 执行器就是逻辑 PEP |
| 部门 Runtime 容器拆分、服务间 mTLS | 单体内通过数据权限区分中央 Agent / 部门 Agent 就够；没有独立信任边界要 mTLS 保护 |
| 每部门独立数据库 / 独立 VPC | 单企业内部部门隔离 ≠ 多租户互不信任；物理隔离留作数据模型上"以后能扩"，不是现在就建 |
| Hash 链 + WORM 审计 | 应用账号只给审计表 INSERT 权限 + 每日导出到开版本控制的 OSS，已能做到"改了能发现"，链式签名是等保/合规硬要求出现后再加的量级 |
| 多企业租户管理 | 产品前提是单企业，不做 |

### 9.2 保留并要长期遵守的原则

- 默认拒绝；每次请求重新鉴权；客户端传的 `organization_id`/`team_id`/角色一律不可信，
  后端按登录用户重新计算（`access_control.py` 现在就是这么做的，继续这个模式）。
- 中央 Agent 不能扩大用户权限，不能把用户无权访问的部门知识库偷偷加入检索范围。
- 高风险操作必须审批 + 二次认证；所有关键操作要能审计。
- 单体 + 逻辑隔离是**当前**选择，不是把物理隔离的路堵死——数据模型留 `organization_id`/
  `team_id`，HR/财务/法务这类高敏部门将来要拆独立部署时，不需要重新建模型。

### 9.3 核对代码后的修正（方案里几处假设跟实际代码不一致）

方案假设的几个资源目前的真实状态（读 `models/init_db.py` 核对过，不是凭印象）：

| 方案里提到的资源 | 实际情况 |
|---|---|
| `Agent` | 没有 `organization_id`/`team_id`/`scope_type`/`sensitivity`，需要新加 |
| `Skill` | 没有 `organization_id`/`team_id`/`scope_type`/`sensitivity`；现有 `is_public`（0/1）后续由 `scope_type` 取代 personal/department/enterprise 三态 |
| `KnowledgeSpace` | Phase 3B 已经有 `team_id`/`organization_id`（`fk_kspace_team`/`fk_kspace_org`），缺 `scope_type`/`sensitivity` |
| `Prompt` | 项目里没有独立 `Prompt` 表，`Agent.prompt_file` 只是个路径列——"Prompt 跟随 Agent"已经是事实，不用新建表 |
| `Conversation`/`Message`/`BackgroundTask` | 都已经通过 `agent_id`/`user_id`（`BackgroundTask` 还有 `target_type`/`target_id`）间接继承所属资源的权限，不需要加归属字段，只要 `access_control.py` 覆盖到位 |
| `Attachment` | 代码库里不存在这张表，方案里"附件继承会话权限"这条现在不适用，等真的有附件上传功能再补 |
| `OperationLog` | 是 HTTP 访问日志（method/path/status_code/latency_ms），不是方案要的"操作前后摘要 + 审批单 + Trace ID"业务审计事件；第 9.6 节的 `audit_event` 是全新表，不是扩展它 |

### 9.4 最终架构（落地版）

```
Vue3 → Nginx → FastAPI 单体
  ├── 身份认证
  ├── enterprise_access.py + access_control.py（逻辑 PDP）
  ├── 中央 Agent 编排 / 部门 Agent（同一进程，agent_type 区分）
  ├── 审批服务（新）
  ├── 审计服务（新，追加式）
  └── 数据访问层
        ↓
MySQL + Redis + ChromaDB + Worker
```

`organization` 保留作为企业安全边界（长期只有 1 行也不删表），`teams` 在前端展示为
"部门"，表名不改。

### 9.5 Phase 3D 七阶段范围（按依赖顺序）

| 阶段 | 内容 | 依赖 |
|---|---|---|
| 1 | 资源归属字段：`Agent`/`Skill` 加 `organization_id`/`team_id`/`scope_type`/`sensitivity`；`KnowledgeSpace` 补 `scope_type`/`sensitivity`。`scope_type ∈ {enterprise, department, personal}`，`sensitivity ∈ {public, internal, confidential, restricted}`，创建时由后端算，不接受前端传值 | 无，可以马上开始 |
| 2 | 统一权限入口扩展：`enterprise_access.py`/`access_control.py` 加 `get_access_context`/`authorize`/`list_accessible_agents`/`require_sensitivity_level` 等，覆盖路由、RAG 检索、Skill 绑定、Tool 执行、后台 Worker | 依赖阶段 1 的字段 |
| 3 | 中央 Agent 轻量路由：`agent_type ∈ {central, department, personal}`，规则路由到用户可访问范围内的部门 Agent，记录路由过程 | 依赖阶段 1、2 |
| 4 | 审批 + 二次认证：新表 `approval_request`（绑定具体资源/操作/有效期），高风险操作（删空间、发布高风险 Skill、导出限制级数据、改权限策略）走审批 | 依赖阶段 1 |
| 5 | `row_version` 乐观锁（Agent/Skill/KnowledgeSpace/权限策略/审批记录）+ Agent/Skill 发布生命周期 `draft → reviewing → published → retired`，已发布不能原地改 | 依赖阶段 1 |
| 6 | 追加式 `audit_event` 表（应用账号只给 INSERT，读单独只读权限，删用户不级联删审计），每日导出 OSS | 独立，可以和 4/5 并行 |
| 7 | Phase 4 生产保障：RDS 迁移、备份恢复演练、管理员 MFA、Token 版本撤销、越权自动化测试、压测告警 | 前面阶段完成后 |

### 9.6 决策记录（补充第 4 节）

| 问题 | 决策 |
|---|---|
| PDP/PEP 要不要拆成独立服务？ | **不拆**：`enterprise_access.py` + `access_control.py` 就是逻辑 PDP，继续在这两个文件里扩展，不新建权限微服务 |
| 部门要不要物理隔离（独立库/独立 VPC）？ | **现在不做**，但字段留 `organization_id`/`team_id`，未来 HR/财务/法务要拆独立部署时数据模型不用重建 |
| 审计要不要 Hash 链 + WORM？ | **现在不做**：追加式表 + 数据库权限隔离 + OSS 每日备份先顶上；等保/合规硬要求出现再加链式签名 |
| Skill 的 `is_public` 字段要不要保留？ | **保留字段，语义收窄**：新加 `scope_type` 承担 personal/department/enterprise 三态，`is_public` 后续按 `scope_type == enterprise` 派生，不必现在删列 |

下一步：从阶段 1 开始动手（模型加字段 + Alembic 迁移 + 数据回填/默认值），阶段 2-7
排在后面，逐步来。

## 12. 执行结果（阶段2：统一权限入口扩展，2026-09-27）

阶段1加的 `scope_type`/`team_id`/`organization_id` 现在真正接进了授权判断，覆盖聊天、
会话、记忆、流水线、评估、RAG 检索、Skill 绑定这几条链路。

### 只放宽"用"，不放宽"改"

`service/access_control.py` 新增 `get_usable_agent`/`get_usable_agent_async`：在
`get_owned_agent`（严格 owner）基础上多两条来源——`scope_type="department"` 时该 Agent
所属部门的在职成员、`scope_type="enterprise"` 时任意在职企业成员。改配置类操作
（改名/删除/绑定知识库空间/绑定 Skill 的"改"那一半）继续用 `get_owned_agent`，没有
一并放宽——写权限是阶段4 审批机制要管的事，这次不动。

`can_read_skill` 加了个可选的 `db` 参数（省略时行为跟以前完全一样），只有
`skills_core/binding.py` 里"把 Skill 绑到 Agent"这个场景会传，其余调用点不强行改签名。

已切到新语义的调用点：`chat_service`（聊天）、`conversation_service`/
`conversation_async_service`（建会话/列会话）、`memory_service`/`memory_async_service`
（记忆读写，记忆本身仍按 user_id 过滤，放宽的只是"能不能用这个 Agent"）、
`agent_runtime`（实际执行聊天）、`agent_pipeline_service`（流水线步骤/执行）、
`FasdtApi/evaluation.py`（RAG 评估/固定评估集）、`rag/debug_service`（调试样例）、
`rag/search_entry.py` 的 `search_for_agent`/`search_for_agent_async`（聊天用的检索
入口；`search_scoped`/`search_for_widget` 是给组件平台用的，没有跟着改，见文件内注释）、
`skills_core/binding.py::list_agent_skills`、`skill_async_service.py::list_agent_skills`。

刻意留 owner-only 没动：`FasdtApi/knowledge.py` 里的文档上传/爬取/复制/启停/重建/删除
（全是私有库管理操作）、`knowledge_async_service.py::list_owned_documents`（这个不按
上传者过滤，会把其他人的私有文档也亮出来，不能跟着放宽）、`search_entry.py` 里
`search_scoped` 内部严格 owner 校验（`search_for_agent` 在私有库回退分支——没绑知识库
空间时——仍然会经过它，是个记录在案的窄口径限制：新建的部门共享 Agent 应该绑知识库
空间而不是用私有库，见文件内注释）。

### 顺手挖出的真实缺口：五处重复的权限判断

核对"聊天链路"每一环时发现：`agent_belongs_to_user_async` 这个判断被**独立复制了
五份**——`conversation_async_dao.py`、`agent_run_async_dao.py`、`memory_async_dao.py`、
`skill_async_dao.py`、`knowledge_async_dao.py` 各有一份完全一样的 `Agent.user_id ==
user_id` 查询，各自被对应的 `*_async_service.py` 调用，**全部绕开了
`service/access_control.py`**——这正是设计稿开头就点名要收敛掉的模式（"两套并存迟早
算出不一样的结果"），只是这次不是我们自己写出来的，是 Phase 3B 之前就有的存量代码，
阶段2核对调用链时才挖出来。

不修的后果：即使 `access_control.get_usable_agent_async` 已经放宽了部门/企业共享，
走 `/conversation`（建会话）、`/agent/{id}/runs`（运行轨迹）、记忆管理、Skill 列表这几
条路由的用户仍然会在这五个重复实现那里被拒——"能聊天但建不了会话""能查权限但查不到
运行记录"这种半好半坏的状态，比统一拒绝更容易让人误以为哪里没接对。

已修：四个（conversation/agent_run/memory/skill）的重复实现直接删掉，调用点全部改成
调 `access_control.get_usable_agent_async`；`knowledge_async_dao.py` 那份故意保留
（原因见上面"刻意留 owner-only"）。

新增回归测试 `tests/test_access_control_agent_skill_scope.py`（8 个，真实 DB）：
personal/department/enterprise 三种 scope_type 分别配 owner / 部门在职成员 / 企业在职
成员 / 无关用户的矩阵，同步+异步都测，并显式回归 `get_owned_agent`/`can_read_skill`
不传 `db` 时没有被误放宽。716 个测试全绿，ruff/compileall 干净。

阶段3（中央 Agent 受控路由）还没开始——本次只是让已有的归属字段在授权层生效，
`scope_type` 目前仍然没有任何创建入口能设成 department/enterprise（全部是 ORM 默认值
`personal`），这条路由/创建能力留给阶段3 一起做，不在这次里超前实现。

## 13. 执行结果（阶段4/5/6：审批 + 乐观锁/发布生命周期字段 + 通用审计，2026-09-27）

跟用户核对过阶段3的前置问题：中央 Agent 路由要路由到真实的部门 Agent，但那些部门
Agent 的业务能力依赖 Phase 5（Spring Boot 企业业务中心，还没开始）——阶段3现在做只是
搭一个指向空气的路由骨架。用户选择**先做阶段4-6**，这三项不依赖部门 Agent 是否存在。

### 阶段4：审批（`approval_request` 新表 + `service/approval_service.py`）

绑定具体 `(action, resource_type, resource_id)`，不是一句笼统的"同意"：`request_or_get_pending`
（申请，同一个资源+操作有未过期的在途单就复用，不重复建单）、`decide`（企业管理员批准/拒绝，
决定过的单子不能再决定第二次）、`try_consume_approved`（认领一条 approved 且没消费过的单子，
消费后置 `executed_at`，不能被消费第二次）。企业管理员判定新增
`service/enterprise_access.py::require_org_role_async`（`require_org_role` 的异步版，
给这次全异步的审批路由用，避免为了一个权限检查硬塞同步 Session）。

新路由 `FasdtApi/approval_route.py`（`GET /approvals/pending`、
`POST /approvals/{id}/decide`，都要求 `require_org_role_async("admin")`）——这是
`require_org_role`/`require_org_role_async` 自 Phase 3B 落地以来**第一次真正被路由用到**
（之前设计稿写的时候就说了"这一步只落地函数本身，还没接到任何路由上"）。

唯一接了审批的真实高风险操作：`space_async_service.delete_space`（删知识库空间）。
第一次调用只建审批单、返回"待审批"，不删数据；企业管理员批准后，用户重新调一次
删除接口，`try_consume_approved` 认领那条单子才真的执行删除。文档里列的其余几项
（发布高权限 Skill、导出限制级数据、改权限策略）现在都没有对应的真实路由——Skill
按部门发布是阶段3D未做的业务功能，导出/权限策略管理路由压根不存在——所以先接
唯一一个已经存在的真实场景，其余等对应功能真的做出来时照这个模式接。

### 阶段5：乐观锁 + 发布生命周期（只加字段，不接强制逻辑）

`agent`/`skill` 加 `row_version`（默认0）+ `lifecycle_status`（默认`draft`，
draft/reviewing/published/retired）；`knowledge_spaces` 只加 `row_version`（空间
已经有 `status` 管 active/archived，不是 draft/published 那一套，不需要
`lifecycle_status`）。

刻意不接比对/强制逻辑：SQLAlchemy 原生的 `version_id_col` 能免手写 compare-and-swap
代码就拿到乐观锁保护，但它要求这张表的每一条更新路径都走 ORM 属性赋值 + commit，
没审计过全部调用点（尤其是有没有绕过 ORM 的原始 SQL UPDATE）就启用，一旦漏了一条，
下次 ORM 更新会莫名其妙报 `StaleDataError`，把没有关系的现有功能炸掉——现在没有任何
前端会传 `row_version`，强行接上收益是零、风险不是零。跟阶段1同一个节奏：先落字段，
等真的有发布评审 UI、需要"已发布不能原地改"这条约束时再接真正的比对逻辑。

### 阶段6：通用审计（`audit_event` 新表 + `service/audit_service.py`）

核对的时候发现项目里已经有一张 `kb_audit_log`（知识库空间模块早先自己建的，范围限定
在 space/document/member/binding）——没有把它泛化，新建了一张不冲突的 `audit_event`
给它覆盖不到的操作用（目前是审批单的申请/批准/拒绝），避免为了"统一"去动一张已经在
正常工作的表。`service/audit_service.py::record[_async]` 是唯一读写入口，`try/except`
+ 独立 `rollback()` 包一层（跟 `space_async_service.py::_audit` 一样的尽力而为写法），
审计失败不能拖垮主流程。

### 测试 + 顺手补的测试基建缺口

新增 `tests/test_approval_service.py`（11 个，真实 DB，全异步）：审批单生命周期
（申请去重/批准/拒绝/过期/消费且只能消费一次）、`require_org_role_async` 三种角色
矩阵、`delete_space` 完整走一遍"第一次只建单不删 → 批准 → 第二次真的删除"。

顺手发现 `tests/_route_client.py::_purge_users` 少清理了一张新表：`approval_request`
的 `applicant_id`/`approver_id` 都是 `user.id` 的外键，测试建完审批单后如果不先删这张
表就删测试用户，会撞 FK（被 `try/except` 悄悄吞掉，测试用户没删干净但不报错）——已经
在删 `organization_members`/`team_members` 那两行旁边补上。

727 个测试全绿，ruff/compileall 干净。`tests/test_routes_isolation.py` 里已有的
`DELETE /knowledge-spaces/{id}` 断言（只查 `status_code == 200`）没有改：接了审批后
第一次删除仍然返回 200（body 变成"待审批"而不是"已删除"），断言本身不会失败，实际
数据没删掉但 `rc.cleanup()` 的 `tearDownClass` 会用裸 SQL 无条件清掉测试数据，不会
跨测试进程泄漏——这个新行为的真实验证放在了 `test_approval_service.py` 里，没有去改
`test_routes_isolation.py` 那边的既有断言。

## 14. 执行结果（阶段3：中央 Agent 受控路由，2026-09-27）

Phase 5 的 OA 请假闭环落地后，HR Agent 第一次有了真实业务能力（见
docs/enterprise-business-hub-plan.md 第12节），阶段3终于有真实目标可以路由，回头补上。

### 加了什么

`agent` 新增 `agent_type`（central/department/personal，默认 personal）+
`department_code`（hr/procurement/sales/finance/it，可空）。`service/runtime/
central_router.py`：`match_department(message)` 纯函数按关键词判断部门（跟设计稿
第6节的对应关系一致：请假/入职/制度→HR，库存/供应商/采购→采购，客户/联系人/
商机→销售，预算/报销→财务，账号/故障/工单→IT）；`resolve_target_agent[_async]`
只在**新建会话**时路由一次（`conversation_id is None`），命中部门后在当前用户能用
的 Agent 里找那个部门的（复用阶段2的 `get_usable_agent[_async]`，不重新写一套
可见性判断），找到就路由过去，找不到（没有这个部门的 Agent，或者有但当前用户用
不了）就中央 Agent 自己回答。已有会话（`conversation_id` 有值）直接沿用创建时定下
的 `agent.id`，不重新路由——`conversation.agent_id` 是很多地方依赖的既有约束，
阶段3不碰这个约束。

接入点：`FasdtApi/chat.py` 的 `chat`/`chat_stream` 两个路由处理函数，在算出
`user_message` 之后、调 `chat_service.chat_with_agent[_stream_async]` 之前插一句
路由解析，把返回值当成真正要用的 `agent_id`。**没有改 `chat_service.py`/
`agent_runtime.py` 一行代码**——这两个是全项目最重、测试最多的热路径，阶段3选择
在路由层做一次"选哪个 agent_id"的前置决策，而不是钻进热路径内部改，把新逻辑的
风险面限制在一个新文件 + 两行调用。

`Agent` 创建路由（`POST /agent`）新增可选的 `agent_type`/`department_code` 参数
（`service/agent_service.py::create` 校验枚举值合法性），不传就是现存行为
（personal/NULL）——阶段1定下的"字段先加，创建入口跟着当次一起给"的节奏，跟阶段5
的 `row_version`/`lifecycle_status`（故意不给创建入口，等真有发布场景）是两种不同
的判断：这次给入口是因为不给的话路由逻辑完全没有办法被真实验证。

### 为什么现存行为不受影响

`agent_type` 默认值是 `personal`，`resolve_target_agent`/`_async` 对非 `central`
类型的 Agent 直接原样返回 `agent_id`，是纯粹的空操作——现存的所有 Agent（包括所有
测试建的）都是这个默认值，`/chat/{id}` 的行为在没有人显式创建 `agent_type="central"`
的 Agent 之前完全不变。727 个既有测试全绿就是这条的证据；新增 `tests/
test_central_router.py`（15 个，7 个纯函数关键词匹配 + 8 个真实 DB 场景：路由成功、
命中部门但没有对应 Agent、命中部门但对应 Agent 不属于当前用户可用范围、没命中任何
关键词、已有会话不重新路由、同步+异步各测一遍非 central Agent 不受影响）显式回归
这一条。737 个测试全绿，ruff/compileall 干净。

### 还没做

采购/销售/财务/IT 四个部门目前都没有真实的 Agent 后端能力（只有 HR 有 Phase 5 的
OA 请假工具）——路由规则本身是完整的（`_ROUTING_RULES` 五个部门都列了），但除了
`hr` 之外命中了也找不到可用的部门 Agent，会退回中央 Agent 自己回答，这是设计内的
优雅降级，不是 bug。等 Phase 5 的采购/CRM 模块做出来，对应部门就自动能被路由到，
不需要再改 `central_router.py`。

## 15. 决策修正：`POST /agent` 不再接受 `agent_type`/`department_code`（2026-09-28）

第14节当时的决定是"给创建入口，不然路由逻辑没法被真实验证"——这个判断本身没错，
但给的方式错了：直接把这两个字段挂在**普通用户**的创建接口（`AgentCreate`）上，
没有做任何权限校验。复查发现：任何登录用户都能在自己创建 Agent 时传
`agent_type="central"` 或 `agent_type="department", department_code="procurement"`，
把一个个人 Agent 伪装成中央/部门 Agent。当时能验证路由逻辑是因为测试直接用 ORM
构造 `Agent(...)`（见 `tests/test_central_router.py`），根本不需要开放这个公开接口——
这个入口从一开始就是不必要的攻击面，不是"先给着，以后再收紧"的权衡。

现状（`get_usable_agent` 只按 owner/部门成员/企业成员判可见性）下，自己伪造的
"部门 Agent"因为没有配套的 `team_id`/`organization_id`/`scope_type`（这三个字段
"由后端按当前用户/目标 team 计算写入，不接受前端传值"，`models/init_db.py` 里
`Agent.organization_id` 那行注释写的就是这个意思，`agent_type`/`department_code`
应该跟它们同等对待，之前漏了），`scope_type` 还是默认的 `personal`，所以中央路由
只会把这个人自己路由到自己伪造的 Agent，没有跨用户访问——但语义上完全不该允许，
不能靠"目前恰好没危害"当长期设计。

**修复**：`FasdtApi/agent.py::AgentCreate` 去掉这两个字段，`create_agent`路由不再
转发它们。真正需要建中央/部门 Agent 时，走企业管理员专用的组织管理后台（见第16节
起，`FasdtApi/organization_admin.py`），后端一次性把 `agent_type`/`department_code`
连同 `organization_id`/`team_id`/`scope_type` 一起算好、一起写，不接受任何字段
单独由前端指定。`service/agent_service.py::create`/`models/agent_dao.py::create_agent`
的函数签名不变（组织管理后台的创建逻辑要复用），只收紧了普通用户能摸到的那个入口。
新增回归测试 `tests/test_routes_isolation.py::
test_create_agent_ignores_client_supplied_agent_type_and_department_code`：传了这两
个字段，建出来的 Agent 仍然是 `agent_type="personal"`/`department_code=None`。

## 16. 修复：多部门用户的 team_id 取错部门（2026-09-28）

`service/enterprise_hub_client.py::resolve_caller_context`（第0节 P0 修复引入）原来
直接 `SELECT team_id FROM team_members ... ORDER BY id LIMIT 1`——一个人如果同时是
两个部门的负责人，永远只能拿到"最先加入的那个"，跟当前到底在处理哪个部门的事务
完全无关。后果：部门负责人管两个部门时，通过其中一个部门的 Agent 操作，可能被
签成另一个部门的 `team_id`，审批会被 Java 侧新加的 `TeamAccessGuard`（见
docs/enterprise-business-hub-plan.md 第16节）当成跨部门拒掉；创建采购单/请假单也
可能被记成错的部门。

**修复**：`resolve_caller_context(user_id, agent_id=None)` 新增 `agent_id` 参数，
`team_id` 推导顺序变成——1. 当前 Agent 自己的 `team_id`（仅当
`agent.agent_type=='department'`，这种 Agent 本来就只服务一个部门，用它的
`team_id` 比猜用户"在职的第一个部门"准确得多，通过对应部门 Agent 操作时这条就够）；
2. 没有部门 Agent 上下文（中央/个人 Agent 直接调用）才退回旧的"第一个在职部门"
兜底。`service/tools/oa_leave.py`/`procurement.py` 全部改成把 `ctx.agent_id` 传进去。

这不是"支持用户显式选工作部门"那个更大的功能（还没做，仍然是已知限制：完全脱离
部门 Agent、直接跟中央/个人 Agent 对话处理第二个部门的事务，仍然会退回第一个部门）——
只解决"通过正确的部门 Agent 操作"这条主路径，跟 CRM/OA/采购现在的实际使用方式
（用户找对应部门的 Agent 聊）一致。

新增 `tests/test_enterprise_hub_client.py`（4个，真实 DB）：造一个同时管两个部门的
用户，验证"通过 team_b 的部门 Agent 操作拿到 team_b"、"没有 Agent 上下文/个人
Agent/不存在的 agent_id 都退回 team_a（旧兜底行为不变）"。

## 17. 企业组织管理后台（2026-09-28）

数据库早就有 `Organization`/`Team`/`EnterpriseRole`/`OrganizationMember`/`TeamMember`
（Phase 3B/3D），但从来没有对应的管理入口——建部门、分配员工、设负责人、调企业角色
只能靠迁移脚本或直接改库。复查指出这是"中央 Agent 管理部门 Agent"这个目标当前最大
的产品缺口，这次补上。

### 加了什么

**后端**：新增 `service/organization_admin_service.py`（纯 service 层，全部
AsyncSession）+ `FasdtApi/organization_admin.py`（路由，`/admin/org/*`）：

- 部门：`GET/POST /admin/org/teams`（列表/新建）、`PATCH /admin/org/teams/{id}`
  （改名/启停）、`GET /admin/org/teams/{id}/permissions`（部门权限关系总览：
  成员+角色、绑定的知识库空间、绑定的部门 Agent）。
- 部门成员：`GET/POST /admin/org/teams/{id}/members`（列表/分配，`role_code=admin`
  即设为部门负责人）、`PATCH`/`DELETE .../members/{user_id}`（改角色/移出）。
- 企业成员（组织维度，独立于具体部门）：`GET/POST /admin/org/members`、
  `PATCH`/`DELETE /admin/org/members/{user_id}`（调整企业角色、启停、移出）。
- `GET /admin/org/roles`：企业角色目录（organization/team 两个 scope），给前端
  角色选择器用，不在前端硬编码角色列表。

给部门分配成员时会顺带自动补一条 `organization_members`（默认 `member` 档，已有
更高角色不动）——单企业部署下"在某个部门里"本来就该隐含"是企业成员"，不用管理员
先手动加一遍企业成员再加部门成员。移出企业时反过来级联清掉这个人在所有部门里的
身份，避免"不是企业成员但还挂在某个部门"的悬空状态（这个悬空状态会跟
`service/enterprise_access.py::require_team_role` "先查企业成员再查部门角色"的
既有假设冲突）。

**权限网关**：跟 `FasdtApi/admin.py` 其它所有端点一样走平台超级管理员
（`get_current_admin_user_async`），不是企业内部的 `require_org_role("admin")`——
单企业私有部署下操作这个后台的就是平台管理员本人，没必要引入第二套权限入口。
`is_org_admin`/`is_team_admin`（P0 修复引入）仍然是聊天/审批链路判断"企业管理员"/
"部门负责人"的唯一依据，这里只是给这两个角色**赋值**的地方。

**前端**：新增 `frontend/src/views/admin/AdminOrganization.vue`（路由
`/admin/organization`，侧栏"组织架构"），两个 tab：
- 部门管理：左侧部门列表（新建/改名/启停），右侧选中部门的成员表格（分配成员/改
  角色/移出）+ 权限关系（绑定的知识库空间、部门 Agent）。
- 企业成员：扁平列表，添加/改企业角色/启停/移出。
两个"添加成员"弹窗共用同一个按用户名搜索 `/admin/users` 的选人逻辑。

### 测试

`tests/test_organization_admin_service.py`（14个，真实 DB，直接调 service 函数，
跟 `test_admin_plan_crud.py` 同一套写法）：部门增删改、重名冲突、成员分配/改角色/
移出、"分配成员自动补企业成员身份"、"移出企业级联清团队身份"、权限关系视图、角色
目录形状、各类非法输入/不存在资源的错误分支。`tests/test_routes_isolation.py`
新增 2 个路由级烟雾测试（非管理员 403、管理员走完整个 HTTP 流程）。全量 808 个
Python 测试全绿。

前端：`npm run build`（含 vue-tsc 类型检查）通过；真实起 FastAPI + Vite dev server，
用真实浏览器登录已有的 `admin` 账号，走完整个"新建部门 → 搜索用户 → 设为部门
负责人 → 部门列表/详情实时反映 → 企业成员列表同步出现"的流程，网络请求全部
200（`read_network_requests` 确认），不是只看 UI 截图。验证用的测试部门已经清理。

### 还没做

删部门（只做了启停，物理删除留给真有需求再加，防止误删连带的知识库/Agent 绑定
失去归属）；企业角色的"至少要有一个 owner"这类不变量校验（现在允许把最后一个
owner 降级，单企业部署下这是管理员自己的操作，先不加这层保护）；给这个后台单独
接一套企业内部权限（`require_org_role`），如果以后要支持"委托非平台超级管理员的
人管部门"，需要重新设计这一层。

## 18. P0：停用部门/企业不生效 + 平台审批双人制/原子消费（2026-09-28）

组织后台上线后立刻复查出一个严重问题：**管理员点"停用部门"之后，成员权限完全
不受影响**——后台只改了 `teams.status`，但整条鉴权链路（`service/enterprise_access.py`
的 `_org_role_rank`/`_team_role_rank`、`models/enterprise_dao.py` 的
`is_team_admin_of_team`/`is_team_member_of_team`/`is_org_member`/
`list_space_ids_where_team_admin`）全部只查 `team_members.status`/
`organization_members.status`，从来没有 JOIN `teams`/`organizations` 检查它们
自己的 `status`。第17节的组织后台等于是给一个不存在的功能做了个能点的按钮——
这个漏洞本来就在，只是之前没有触发它的入口，这次是我自己捅出来的窟窿，必须
在同一批里补上。

同时复查还发现两个平台审批（`service/approval_service.py`）的问题：`decide()`
不检查 `approver_id != applicant_id`（知识空间所有者同时是企业管理员时能自己
批自己）；`try_consume_approved()` 是"先查后改"两步，并发下可能被消费两次。

### 修复

1. **停用立即失效**：`_org_role_rank`/`_org_role_rank_async` JOIN `organizations`
   加 `status='active'`；`_team_role_rank` JOIN `teams` 加 `status='active'`；
   `require_team_role` 自己单独查的 `team_org_id` 也加上同样的条件。
   `models/enterprise_dao.py` 的四个 SQL 常量全部加上对应的 JOIN。
   `service/enterprise_hub_client.py::resolve_caller_context` 的 team_id 推导
   （部门 Agent 自己的 team_id、用户在职部门兜底）两处都跳过已停用的部门，
   不会解析出一个"名义上还在，实际已经死了"的 team_id。`service/tools/crm.py`
   顺手一起改成调用 `resolve_caller_context(user_id, agent_id)`（不再自己另写
   一份 `_resolve_team_id`），这同时也是第16节多部门修复漏掉的一环
   （CRM 之前一直没跟上 OA/采购那次改动）。
2. **禁止自审批**：`approval_service.decide()` 开头加
   `if approver_id == row.applicant_id: raise PermissionDenied(...)`——角色门槛
   （`require_org_role("admin")`）挡不住"申请人自己就是管理员"这种情况，必须
   单独判断。
3. **原子消费**：`try_consume_approved()` 从"SELECT 检查 + 单独 UPDATE"改成
   条件 UPDATE（`WHERE id=:id AND executed_at IS NULL`），受影响行数为 0 就说明
   被别的并发请求抢先消费——UPDATE 语句本身对目标行是加锁的"当前读"，不是 SELECT
   那种快照读，数据库保证只有一个事务能真的把 `executed_at` 从 NULL 改成非 NULL。

### 测试

新增 `tests/test_enterprise_dao.py`（3个）直接测四个 SQL 常量在部门/企业停用后的
行为；`tests/test_enterprise_access.py` 新增 3 个（`_org_role_rank`/`_team_role_rank`/
`require_team_role` 各一个停用即失效场景）；`tests/test_enterprise_hub_client.py`
新增 2 个（部门 Agent 自己的团队被停用、兜底团队被停用，两条推导路径都要跳过）；
`tests/test_organization_admin_service.py` 新增 1 个端到端测试，直接调用组织后台
的 `update_team(status="disabled")`，验证 `is_team_admin` 立刻翻转为 False——
不是只测底层 SQL，是测这个后台功能真的做了它声称要做的事。`tests/test_crm_tools.py`
按 oa_leave/procurement 的模式重写。`tests/test_approval_service.py` 新增自审批
拒绝测试 + 一个真并发测试（`asyncio.gather` 两个独立会话同时 `try_consume_approved`
同一条审批单，断言恰好一个成功一个返回 None——不是顺序调用两次，是真的并发）。

全量 820 个 Python 测试全绿，ruff 干净。

## 19. Skill 发布生命周期 + row_version 乐观锁落地（2026-09-28）

`lifecycle_status`/`row_version` 从阶段5（第13节）加字段以来一直是"只加字段，
零强制逻辑"——草稿 Skill 照样能被任何人绑定和运行，并发编辑也不会互相覆盖检测。
这次把 Skill 那一半接上（Agent 那一半留到第20节，跟"中央/部门 Agent 管理后台"
一起做，因为 Agent 的发布状态本来就是给那个后台的路由门禁用的）。

### 发布状态管的是什么

新增 `service/lifecycle.py` 定义共用常量（`VALID_LIFECYCLE_STATUSES`、
`BINDABLE_BY_OTHERS_STATUSES={"published"}`、`RETIRED_STATUS`），Agent 和 Skill
共用同一份，不各自定义一遍。规则很简单：

- **作者自己**：任何状态的 Skill 都能绑到自己的 Agent 上试跑（草稿存在的意义就是
  给作者自己验证），退役的除外——退役是彻底停用，连作者自己都不再加载。
- **除作者外的任何人**（哪怕是公开/部门/企业共享范围内）：只有 `published` 状态
  才能绑定。这条通过新增 `service.access_control.can_bind_skill()` 实现，是跟
  `can_read_skill`（判断"看不看得到"）平行的一层判断（"绑不绑得了"），不混在一起。
- **运行时**（`service/skills_core/binding.py::get_agent_skills_merged_config`，
  ToolExecutor 的唯一 Skill 加载入口）：跳过所有 `lifecycle_status == "retired"`
  的已绑定 Skill，不管是谁的 Agent。已绑定关系不用跟着解绑（后台随时能重新发布），
  只是运行时不加载。
- `GET /skill/public`（普通用户浏览公开 Skill 的唯一入口，同步+异步两版 DAO）
  改成只返回 `is_public=1 AND lifecycle_status='published'`——列出来一个绑不了
  的选项没有意义。管理员的 `GET /skill/`（`list_all_skills`）不受影响，仍然看
  全部，含草稿。

### 乐观锁怎么做的

`models/skill_dao.py::update_skill` 新增可选参数 `expected_row_version`：不传
就是旧行为（不比对版本，兼容 import/translate/reanalyze 这些还不知道版本号概念
的既有调用方）；传了就是一条 `UPDATE ... WHERE id=:id AND row_version=:expected`，
受影响行数为 0 就抛 `Conflict`——原子的"比对+写入"，不是先 SELECT 版本号再在
Python 里比较再 UPDATE（那样两个并发请求可能都读到同一个旧版本，都以为自己没
冲突，跟 `service/approval_service.py::try_consume_approved`（第18节）是同一个
模式）。`FasdtApi/skill_route.py::SkillUpdate` 新增 `expected_row_version`/
`lifecycle_status` 两个可选字段，`_skill_to_dict` 现在把这两个字段一起吐给前端
（之前完全没有任何响应体暴露过它们）。前端 Skill 管理页面还没有对应的版本冲突
提示 UI（提交时传 `expected_row_version` 才会触发乐观锁，不传就还是旧行为），
留作后续——后端保证已经生效，不依赖前端配合。

### 测试

新增 `tests/test_skill_lifecycle.py`（18个）：`can_bind_skill` 纯逻辑单测
（作者/非作者 × 四种状态的矩阵）；真实 DB 的绑定门禁集成测试（非作者绑草稿被拒、
绑已发布通过）；真实 DB + 临时 SKILLS_ROOT 的运行时测试（已退役 Skill 被排除在
`get_agent_skills_merged_config` 的结果外，即便是作者自己的 Agent）；乐观锁
集成测试（版本匹配成功并自增、版本过期抛 `Conflict`、不传版本号维持旧行为、
非法 `lifecycle_status` 值被拒）。

`_skill_to_dict` 新增两个字段暴露了一批既有测试用 `SimpleNamespace` 模拟 Skill
ORM 对象的盲点——`test_skill_package_import.py`/`test_skill_script_policy.py`/
`test_skill_script_report.py`/`test_skill_sandbox.py`/`test_skill_versions.py`/
`test_skill_import_export.py` 里手搭的假 Skill 对象都缺这两个字段，补上后全部
恢复通过（补的时候顺手发现 `git stash` 在这台 Windows 机器上因为 CRLF/LF 换行符
差异，对几个跟这次改动完全无关、老早就脏着的文件报"合并冲突"——用
`git checkout stash@{0} -- <具体文件>` 精确取回自己改的 7 个文件，绕开了那些
无关文件，没有丢东西也没碰它们）。

全量 838 个 Python 测试全绿，ruff 干净。

### 还没做

~~前端 Skill 管理页面暂时还没有发布状态的切换 UI 和乐观锁冲突提示~~ **已完成**
（2026-09-28）：编辑弹窗新增"发布状态"下拉（draft/reviewing/published/retired），
卡片上加了状态徽章；勾了"公开给其他用户使用"但状态不是 published 时给出提示
（因为 `list_public_skills` 要求两者同时满足才会出现在能力商店）。乐观锁冲突：
保存时带上打开弹窗那一刻读到的 `row_version`，冲突时后端 409 的具体消息直接
展示给用户，并自动用最新数据刷新弹窗（不用用户自己关掉重开）。真实浏览器 +
真实本机 MySQL 验证：新建一个测试 Skill，改成"已发布"+"公开"，保存后直接
查库确认 `lifecycle_status='published'`、`row_version` 正确自增；又故意在弹窗
打开期间从后台把同一行的 `row_version` 改掉，模拟并发修改，确认保存时前端
准确弹出"Skill 已被其他人修改（当前版本 X，你读到的是 Y）"并自动刷新到最新值。
顺带发现：本机导入的 75 个技能包全部还是 draft，`公开` 勾选了也不会出现在
能力商店里——这正是这个 UI 缺口过去会让管理员误以为"已经公开"的真实场景。

## 20. Agent 发布生命周期 + 中央/部门 Agent 管理后台（2026-09-28）

补第19节留的那一半，也是"中央 Agent 管理部门 Agent"这个目标下最后一块缺口：
`agent_type`/`department_code`/`organization_id`/`team_id`/`scope_type` 字段
Phase 3D 阶段1/3 就有了，中央路由（`service/runtime/central_router.py`）也认
这些字段，但一直没有创建/维护它们的管理入口，只能直接改数据库；路由本身也没有
检查 `lifecycle_status`，草稿状态的部门 Agent 会被当成可路由的目标。

### 路由门禁

`central_router.py` 的 `resolve_target_agent[_async]`（中央 Agent 本身）和
`_find_department_agent[_async]`（部门 Agent 匹配的 SQL）都加了
`lifecycle_status='published'` 检查——跟 Skill 的门禁是同一个思路：管理员可以
先建、先配 prompt、先测，确认没问题再发布，不会一建好就立刻影响真实用户的路由。
`tests/test_central_router.py` 的既有 fixture（`central`/`hr_dept`/
`unowned_hr_dept`）补上了显式 `lifecycle_status="published"`（不然默认 draft，
这次改动会让这些原本测"该不该路由"的用例全部失败，因为都卡在发布状态门槛上），
新增 `draft_dept`/`test_draft_central_agent_does_not_route_at_all` 专门测这条
新加的门禁本身。

### 管理后台

新增 `service/agent_admin_service.py` + `FasdtApi/organization_admin.py` 里
`/admin/org/agents` 系列端点（跟第17节的部门/成员管理同一个路由文件、同一个
权限网关——平台超级管理员）：

- `GET /admin/org/agents`：列出所有中央/部门 Agent（不含普通用户的 personal
  Agent），带绑定的部门名。
- `POST /admin/org/agents`：创建。`agent_type=department` 时强制要求
  `department_code`（校验合法值，复用 `central_router.VALID_DEPARTMENT_CODES`，
  不再定义一份）+ `team_id`（必须是已存在且未停用的部门）；`agent_type=central`
  时这两个字段强制清空（`scope_type` 相应设成 `enterprise`/`department`）。
  新建的 Agent 一律 `lifecycle_status='draft'`，不接受创建时直接发布。可以带
  `role`/`task`/`constraints`/`output` 一起把 prompt YAML 建好。
- `PATCH /admin/org/agents/{id}`：改名/改模型/改绑部门/改发布状态，
  `role`/`task`/`constraints`/`output` 部分更新时会先读旧的 prompt YAML 再合并
  写回（`update_prompt_file` 是整份覆盖，不是 PATCH 语义，服务层必须自己做合并，
  不然只传 `task` 会把已经配好的 `role` 冲掉）。`row_version` 乐观锁：不传
  `expected_row_version` 就是旧行为，传了就是条件 UPDATE，版本不对抛
  `Conflict`——跟 Skill（第19节）、审批（第18节）同一个模式，只在这个"多个
  管理员协作编辑同一个央/部门 Agent"的场景接入，不碰
  `service/agent_service.py`/`models/agent_dao.py` 那条给普通用户个人 Agent
  用的既有更新路径（单一所有者编辑，并发冲突风险低，且是全项目测试最多的热
  路径之一，没必要为了这个场景去改）。

`Agent.skills` 是 `lazy=False`（联表预加载），查询完整 `Agent` 实体的
`Result` 必须先 `.unique()` 再 `.scalars()`/`.scalar_one_or_none()`，不然
SQLAlchemy 直接报错——这个坑在写 `list_managed_agents`/`update_managed_agent`
时踩到过，两处都已经处理。

前端：`AdminOrganization.vue` 新增第三个 tab"Agent 管理"，表格列出所有央/部门
Agent（名称/类型/所属部门/发布状态徽章/操作），新建/编辑走同一个弹窗（类型选
central 还是 department，department 时联动显示部门代码 + 部门下拉），发布/
停用是行内按钮直接调 `PATCH`。`frontend/src/api/organizationAdmin.ts` 新增
`ManagedAgent`/`listManagedAgents`/`createManagedAgent`/`updateManagedAgent`。

### 测试

新增 `tests/test_agent_admin_service.py`（9个，真实 DB）：创建中央/部门 Agent、
非法 `agent_type`/`department_code`、部门 Agent 缺 `team_id`/绑定不存在的部门、
列表、更新（改名/改绑部门/发布）、乐观锁冲突、非法 `lifecycle_status`。其中
"创建部门 Agent 并绑定"这条会先确认本机的默认企业就是测试自己建的那个企业才跑，
不是就 skip——`_get_default_organization` 取的是"id 最小的那个 Organization"，
测试库和真实开发机的默认企业不是同一行，不能硬编时期望这条总能跑通。

除了单测，还用真实起的 FastAPI + 真实浏览器做了两轮端到端验证：一轮是直接拿
真实默认企业（这台机器上 `organizations.id=1`）走 `curl` 建部门 → 建部门
Agent → 发布 → 列表确认状态同步，全部走真实 HTTP + 真实 DB；另一轮是在浏览器里
真的点"新建 Agent"填表单提交，确认建出来的中央 Agent 显示"草稿"状态且
编辑/发布按钮都在，网络请求全部 200（`read_network_requests` 确认，那次唯一的
401 来自验证开始前一次过期 token 的旧请求，不是这次改动的问题）。两轮验证用的
测试数据都已清理。

全量 850 个 Python 测试全绿（2 个环境相关的条件跳过），ruff 干净，前端
`npm run build`（含 vue-tsc 类型检查）通过。

### 还没做

Agent 生命周期没有做"至少要有一个 published 的中央 Agent"之类的不变量保护
（管理员可以把唯一一个已发布的中央 Agent 退役，路由会全部原样落回中央 Agent
自己回答——这是优雅降级不是崩溃，先不加这层保护）；`department_code` 目前
硬编码在 `central_router.VALID_DEPARTMENT_CODES` 里（hr/procurement/sales/
finance/it 五个），新增部门类型需要改代码，不是数据库配置驱动的，跟
`_ROUTING_RULES` 关键词表是同一个"先用得上，不为假设的扩展性买单"的判断。

## 21. 组织变更审计 + 最后一个 owner 保护（2026-09-28）

组织后台（第17/20节）上线后复查发现：建部门、分配成员、改角色、建/发布 Agent
这些变更操作完全没有留痕，通用审计表（`AuditEvent`，Phase 3D 阶段6）一直只有
`approval_service.py` 一个调用方；而且可以把企业唯一一个 `owner` 降级、停用或
移除，误操作后可能没人能继续管理系统。

### 审计

`service/organization_admin_service.py`/`service/agent_admin_service.py` 的每个
写操作（`create_team`/`update_team`/`add_team_member`/`update_team_member_role`/
`remove_team_member`/`add_org_member`/`update_org_member`/`remove_org_member`/
`create_managed_agent`/`update_managed_agent`）都在自己的 `db.commit()` **之后**
调 `audit_service.record_async`——跟 `approval_service.py` 的既有约定一致：审计
写入失败不该拖垮已经成功的主操作，所以必须在主提交之后才调，不是同一个事务里
先审计后提交。为此给这些函数都加了 `operator_id`（执行操作的管理员 user_id，
之前完全没有这个参数——是谁改的之前根本没留下来），路由层从 `current_user.id`
传入。`update_team`/`update_org_member` 只在真的有字段变化时才写审计（把
"改了但值没变"过滤掉，不然点一次保存按钮不管有没有真改都留一条空审计）。
Agent 发布/退役用专门的 action 名（`org.managed_agent_published`/
`org.managed_agent_retired`），不是笼统的 `updated`——审计列表里一眼能看出
"谁在什么时候发布/停用了哪个 Agent"，不用点进 detail 字段找。

### 最后一个 owner 保护

`organization_admin_service.py` 新增 `_count_active_owners()`，`update_org_member`
（降级角色或停用）和 `remove_org_member` 在"这个人当前是活跃的 owner"且"操作后
就不再是"时，检查企业里是否还剩至少一个别的活跃 owner，没有就拒绝
（`InvalidInput`，提示先把 owner 转给另一个人）。只保护 `owner` 这一个角色——
`admin`/`auditor`/`member` 没有这层限制，降到 0 个 admin 只是少了一些管理能力，
不是"没人能管系统"那种不可恢复的状态。

平台超级管理员（`ADMIN_USER_NAMES` 环境变量配的用户名列表）没有对应的"最后一个"
保护——它是部署配置，不是数据库里的可变记录，这里保护不了，也不属于这个组织
后台的职责范围。

### 测试

新增 `tests/test_organization_admin_service.py::AuditTrailTest`（4个）：建部门/
加成员写审计、停用部门的审计详情里能看到 `{"from": "active", "to": "disabled"}`
这样的前后对比、没有真改动不写审计。`LastOwnerProtectionTest`（4个）：建一个
只有一个 owner 的专属测试企业，测降级/停用/移除唯一 owner 都被拒绝、企业有
第二个 owner 时可以正常操作——这几个测试只有在测试建的企业恰好是这台机器的
默认企业时才会真的跑断言（`_get_default_organization` 取 id 最小的那个
`Organization`，测试库和这台开发机不是同一行），不是就 skip，跟
`test_agent_admin_service.py` 的部门 Agent 场景同一个限制。为了在这台机器上
真正验证过这条保护逻辑本身（不只是"没跑"），另外用一次性脚本 monkeypatch
`_get_default_organization` 指向一个临时建的测试企业，跑通了"降级/移除唯一
owner 都被拒绝"两个断言，验证完立刻清理，没有留任何数据。

`tests/test_agent_admin_service.py` 新增 2 个审计测试。顺手修了
`OrgMemberCrudTest`/`LastOwnerProtectionTest` 用的测试用户名前缀太长
（`rt_{prefix}_{随机后缀}` truncate 到 20 字符时，前缀本身就快用满甚至用满
20 字符，随机后缀被截没了，等于每次跑用的都是同一个用户名——cleanup 一旦失败
就会永久卡住后续所有测试运行）的隐患，缩短成更安全的前缀。

全量 860 个 Python 测试全绿（6 个环境相关的条件跳过），ruff 干净。

### 还没做

~~审计表账号最小权限~~ **已完成**（独立 SQLAlchemy engine/session +
Java 独立 HikariDataSource/JdbcTemplate，`docker-compose.prod.yml`/
`deploy/mysql-init/` 建号脚本 + `scripts/grant_audit_db_privileges.py` 补授权，
真实受限权限账号跑通验证），见第23节。

## 22. 发布自检修好 + Java 测试接入 CI（2026-09-28）

两个纯工程问题，跟企业 RBAC 本身没关系，顺手一起做掉：

**`scripts/release_check.py` 失败**：`REQUIRED_FILES` 里还引用着 4 个已经不存在
的旧迁移文件路径（`migrations/versions/20260830_0001_baseline.py`/
`20260909_0003_user_widgets.py`/`20260910_0005_rag_debug_samples.py`/
`20260911_0006_space_permissions.py`）——2026-09-24 迁移历史重新定过基线（见
`docs/db-migration-plan.md`），这几个文件挪到了 `migrations/archive_pre_baseline/`
只留历史记录，`release_check.py` 没跟着更新。修成引用当前生效的基线文件
（`migrations/versions/20260924_0001_trusted_baseline.py`），另外 3 个纯粹删掉
（它们对应的功能代码文件已经在同一个列表里单独检查过，检查一个早就不参与
`alembic upgrade` 的归档文件本身没有意义）。实际跑了一遍完整脚本（含
`compileall` + 全量 860 个测试 + `npm run frontend:build`）确认真的能跑完，不是
只改路径就当作修好了。

**Java 测试没接入 CI**：`.github/workflows/ci.yml` 新增 `java` job，跟 `backend`/
`e2e` 两个 job 同一个模式——真实 MySQL 服务容器（这次是 `enterprise_business`
库）+ `mvn test`，跑 `enterprise-business-hub` 的全部 35 个测试（28 个真实
HTTP+MySQL 集成测试 + 7 个纯逻辑单测）。之前这些测试只在本机手动跑过，Java
权限逻辑（比如这次这几节加的 `TeamAccessGuard`）如果被后续改坏，没有任何自动化
能拦截。用跟这次 CI 配置完全一致的参数（`ENTERPRISE_DB_HOST=127.0.0.1`，
throwaway MySQL 容器）在本机验证过一遍，35 个测试全部通过，YAML 语法也过了
`yaml.safe_load` 校验。测试结果用 `actions/upload-artifact` 存 Surefire 报告，
跟 `backend` job 存 coverage.xml 是同一个思路。

## 23. 审计表账号最小权限：独立 INSERT/SELECT 专用账号（2026-09-28）

第21节遗留的"还没做"：审计写入（`audit_event` 表，FastAPI 侧 `agent_sql` 库和
Java `enterprise-business-hub` 侧 `enterprise_business` 库各一张）之前复用应用主
账号——能写就能改/删，不是真正的防篡改。这次补上：写入换一个独立连接，接一个
只被授予该表 `INSERT`/`SELECT` 权限的专用 MySQL 账号，没有 `UPDATE`/`DELETE`。

**Python 侧**：新增 `models/audit_db.py`，一套独立的同步/异步 SQLAlchemy
engine/session（`AUDIT_DB_USER`/`AUDIT_DB_PASSWORD`，留空退回主账号
`DB_USER`/`DB_PASSWORD`，本地开发/CI 不受影响）。`service/audit_service.py` 的
`record`/`record_async` 不再接受调用方传入的 `db` 会话——之前签名是
`record_async(db, user_id, action, ...)`，`db` 只是转手传给 `audit_dao`，现在改成
函数内部自己开一个绑定到独立账号的会话，`db` 参数直接去掉（不是留着不用的
兼容性摆设）。3 个调用方（`approval_service.py`、`agent_admin_service.py`、
`organization_admin_service.py`，共 12 处调用）同步去掉了第一个 `db,` 实参。

**Java 侧**：`AuditService` 原来用 `AuditEventRepository`（JPA，走主 `EntityManager`/
主数据源）写入；现在改成自己 `new` 一个 `HikariDataSource` + `JdbcTemplate`（读
`audit.datasource.username`/`password`，`application.yml` 里配的默认值同样是
"留空退回主账号"），直接 SQL `INSERT`。**关键决定：这个 HikariDataSource/JdbcTemplate
不注册成 Spring `@Bean`**——如果注册成 `@Bean DataSource`/`@Bean JdbcTemplate`，
会触发 Spring Boot 的 `DataSourceAutoConfiguration`/`JdbcTemplateAutoConfiguration`
的 `@ConditionalOnMissingBean` 退让逻辑，导致主数据源的自动配置被我的次要数据源
顶替、或者已有测试里未加限定符的 `@Autowired JdbcTemplate` 字段意外注入到审计专用
（权限受限）的那个连接上，跑起来才会在测试清理阶段报 "DELETE command denied"——
这是先想了两版 `@Bean` 方案，意识到会有这个坑之后改的第三版，没有先跑起来发现问题
才改，是看 Spring Boot 自动配置条件注解的文档提前判断出来的。因为不再需要 JPA
实体，`AuditEventRepository.java`、`AuditEvent.java` 两个文件直接删除（唯一的调用方
只有 `AuditService.java` 自己，确认过没有其他地方 `import`）。

**部署**：`deploy/mysql-init/02-create-audit-user.sh` 建账号（`CREATE USER`），不在
这一步授权——实测过 MySQL 的表级 `GRANT` 要求目标表已存在（不存在直接
`ERROR 1146`，会中断整个 `docker-entrypoint-initdb.d` 流程），而这一步跑在容器第一次
启动、Alembic/Flyway 都还没建表的时候。新增 `scripts/grant_audit_db_privileges.py`，
在两边迁移都跑完之后单独执行一次补授权（`GRANT` 本身幂等，可重复执行）。
`docker-compose.prod.yml` 的 `db`/`enterprise-hub` 服务新增
`AUDIT_DB_USER`/`AUDIT_DB_PASSWORD` 环境变量透传，顶部启动步骤注释和
`.env.production.example` 同步更新；`docs/deployment.md` 新增 2.1 节，Docker/
非 Docker 两种部署路径都给了具体命令。

**真实验证，不是只看单元测试**：起了一个 throwaway MySQL 8 容器，完整走了一遍
"CREATE USER（不带 GRANT）→ 建表 → 跑 `grant_audit_db_privileges.py` 补授权 →
用真实 `audit_writer`/密码连接" 的流程：
- Python 侧：用这个真实受限账号跑 `audit_service.record_async()`，确认真的写进去了
  （`SELECT` 能读到刚写的行）；再用同一个账号对该表发 `UPDATE`，确认被 MySQL 拒绝
  （`asyncmy.errors.OperationalError: 1142 UPDATE command denied`），不是靠猜权限
  生效,是拿错误信息实测到的。
- Java 侧：`ENTERPRISE_DB_USER=root`（主账号，JPA/Flyway 用）+
  `AUDIT_DB_USER=audit_writer`（受限账号，`AuditService` 用）同时配置，跑
  `LeaveControllerIntegrationTest` 全部 13 个测试通过——测试自己的
  `jdbc.update("DELETE FROM audit_event ...")` 清理逻辑用的是主账号绑定的默认
  `JdbcTemplate`，这一步能过，反向证明了"不注册成 Spring bean"那个设计决定确实
  避免了两个 `JdbcTemplate` 打架的问题，不是侥幸。
- 直接用 `mysql` 客户端连 `audit_writer` 账号，对 `audit_event` 表分别发
  `INSERT`/`SELECT`/`UPDATE`/`DELETE`，前两个成功、后两个报
  `ERROR 1142 (42000): ... command denied`，确认账号权限精确匹配"只给
  INSERT/SELECT"这个设计目标，不多不少。

跑完整个 35 个 Java 测试 + 47 个相关 Python 测试（`test_agent_admin_service.py`/
`test_approval_service.py`/`test_organization_admin_service.py`，本机 MySQL，
`.venv/Scripts/python.exe -m unittest`）全部通过，确认去掉 `db` 参数、改独立会话
之后没有破坏任何既有行为。

### 还没做

`kb_audit_log`（知识库空间模块自己的审计表，`service/knowledge_space/document_service.py`
里的 `_audit`）没有做同样的账号隔离——这次范围严格对应第21节承诺的
`audit_event` 表，`kb_audit_log` 是另一张表、另一个模块，需要单独评估要不要照
同样的模式做，不在这次顺手带上。

## 24. 第四轮审计 P0：Agent/Skill 生命周期强制在运行时被绕过（2026-09-28）

用户提交的第四份审计报告点出两个 P0：发布生命周期字段只在"入口"查过一次，
没有在"每次真正使用"时复查，导致 draft/reviewing/retired 的 Agent/Skill 在
某些路径下仍然能被继续使用。

### P0-1：部门/企业共享 Agent 的生命周期检查可以被绕过

`service/access_control.py::get_usable_agent`（`chat_service.py`/
`agent_pipeline_service.py`/`memory_async_service.py`/评估路由等 16 处调用方的
唯一权限入口）之前只查"是不是同部门/同企业在职成员"，完全没查
`lifecycle_status`。生命周期检查只在 `central_router.py::resolve_target_agent`
（自动路由挑选目标 Agent 那条路径）里做了——`chat_service.py` 的
`POST /chat/{agent_id}` 直接拿 `resolve_target_agent_async` 算出来的 `agent_id`
去调 `get_usable_agent_async`，如果知道一个还在 draft/reviewing/retired 的部门
Agent 的 ID，企业成员可以绕开路由直接聊。

修复收口到 `get_usable_agent`/`get_usable_agent_async` 这一个函数（两处，同步/
异步各一份）：作者自己（`agent.user_id == user_id`）任何状态都能用，唯独
`retired` 例外（跟 Skill 的 `can_bind_skill` 是同一个道理，草稿要留给作者自己
测）；部门/企业共享的那两条来源（非作者）现在必须 `lifecycle_status in
BINDABLE_BY_OTHERS_STATUSES`（即 `published`）才放行。因为所有 16 个调用方
共用这一个函数，且 `chat_service.py` 每次收到消息都会重新调用它（不是只在
创建会话那一刻查一次），"已有会话继续对话时也要重新校验"这条也顺带自动满足，
不需要在 `central_router.py`/`chat_service.py` 再加代码。

`tests/test_access_control_agent_skill_scope.py` 里部门/企业共享的测试 Agent
之前默认 `lifecycle_status=draft`（字段默认值），补上 `lifecycle_status=
"published"` 才符合新规则；新增两个测试：非作者在 Agent 被改回
draft/reviewing/retired 后立刻不能用，作者自己在除 retired 外任何状态都能用。

### P0-2：Skill 绑定后作者改回未发布，运行时仍会继续加载

`service/skills_core/binding.py::get_agent_skills_merged_config`（`ToolExecutor`
唯一的运行时入口）之前的注释写着"绑定时的 `can_bind_skill` 已经保证了别人绑到
的一定是已发布状态，这里不重复判断"——这个假设是错的：`can_bind_skill` 只在
"绑定那一刻"检查过一次，之后作者把 Skill 改回 draft/reviewing（比如发现问题
想先撤回），已经绑定过它的别人的 Agent 会继续拿旧配置跑下去，运行时从来没有
重新校验过。

修复：`get_agent_skills_merged_config` 现在会查一次绑定它的 Agent 的 owner
（`models.agent_dao.get_agent_by_id`），逐条 Skill 判断"是不是 Agent owner
自己的 Skill"——是，除 retired 外任何状态照常加载（自己拿草稿喂自己的测试
Agent，这条豁免本来就有）；不是（绑定的是别人分享/发布过的 Skill），现在必须
仍然是 `published` 才继续加载，否则跳过（不强制解绑，后台随时能重新发布）。

同时补了审计报告点出的另一半："已发布 Skill 的配置可以直接原地修改，修改会
立即影响所有使用者，没有重新审核过程"——`service/skills_core/crud.py::
update_skill_with_config` 现在只要满足"这次请求带了 `config_fields`（改
system_prompt/tool_names/permissions）" + "Skill 当前是 published" + "请求里没
有一次主动的、跟当前值不同的 `lifecycle_status` 变更"，就会把 `fields` 里的
`lifecycle_status` 强制改成 `draft`，跟其他基础字段的改动走同一次原子更新
（同一次 `row_version` 递增）。判断"是不是主动决定"不能只看请求里有没有带
`lifecycle_status` 这个键——前端编辑弹窗（上一轮加的发布状态下拉）每次保存都
会带上当前选中的值，哪怕没碰过那个下拉框；只有请求里的值跟数据库现有值
**不一样**，才算是管理员自己主动做的状态决定（比如同时把它改成 retired），
此时才不覆盖。编辑弹窗里加了一行静态提示告诉管理员这条规则的存在，不然会
很困惑"为什么改了内容状态就自动变回草稿了"。

真实 DB 测试（新增，均为真实 MySQL，不是纯 mock）：
- `tests/test_skill_lifecycle.py::NonOwnerSharedSkillDemotedAtRuntimeTest`：
  一个 Skill 被作者绑到自己的 Agent、也被另一个用户绑到他们自己的 Agent（绑定
  那一刻是 published）；作者改回 draft/reviewing 后，非作者的 Agent 立刻从
  merged config 里丢了这个 Skill，作者自己的 Agent 完全不受影响。
- `tests/test_skill_lifecycle.py::PublishedSkillAutoDemotesOnConfigEditTest`：
  改一个 published Skill 的 system_prompt 会自动打回 draft；同一次请求里显式
  改成 retired 会尊重这个主动选择，不覆盖；模拟前端每次都重发当前状态值的
  场景（带的还是 published）确认仍然会打回 draft；只改名字不碰运行配置不触发。

改动波及 4 个既有测试文件（`test_skill_sandbox.py`/`test_skill_script_policy.py`/
`test_skill_script_report.py`）——它们用 `SimpleNamespace` 假 `db` +
`patch.object(binding, "dao_list_by_agent", ...)` 只 mock 了 Skill 列表，没
mock 新增的 `get_agent_by_id` 查询，补上匹配的假 Agent owner（跟被测 Skill的
`user_id` 一致，还原"自己绑自己"场景，不影响这些测试原本要测的沙箱/脚本逻辑）
后恢复正常。全量 867 个测试（867 = 860 基线 + 本轮新增 27 个，减掉个别合并）
全部通过。

## 25. 第四轮审计 P1：审批流程的两个并发缺口（2026-09-28）

### P1-6：重复审批单 + 审批决定可被并发覆盖

**问题1（`request_or_get_pending`）**：之前是"先查一遍有没有活跃单，没有才插
一条"，这两步不是原子的——两个并发请求（比如用户手抖连点两次删除确认）可能都
读到"没有活跃单"，都各自插入一条，堆出重复审批单。

修复：`models/init_db.py::ApprovalRequest` 新增 `active_dedupe_key` 列 +
唯一约束（`migrations/versions/20260928_0001_approval_active_dedupe.py`）。
MySQL 的唯一索引允许多个 NULL 共存，只在非 NULL 值之间强制唯一，天然适合
"只对活跃的那一条做唯一约束"——不需要 MySQL 不支持的条件唯一索引，也不用
生成列（生成列没法引用 `NOW()` 判断过期）。这一列完全由 `service/
approval_service.py` 维护：建 pending 单时写成
`f"{action}:{resource_type}:{resource_id}"`；决定为 rejected 或发现已过期时
清空成 NULL（approved 之后仍然占着，直到过期才释放，跟原有 `_ACTIVE_STATUSES`
的语义一致）。并发 insert 时数据库只会让一个成功，另一个撞 `IntegrityError`
后回滚重查，把赢家那一条返回给调用方，调用方无感知，语义上等价于"复用了
一条已有的"。

顺带修了一个跟这个约束直接相关的旧缺口：`request_or_get_pending` 判断"存量单
已过期就不用它"之前只是 Python 里的判断，从没真的把那一行的 `status` 改成
`expired`、也没释放它的 dedupe key——旧单一直悬空占着 pending/approved 状态。
接了唯一约束之后这会直接挡住新单插入，所以这次把它一起结清：判断为过期时
先显式 `UPDATE ... SET status='expired', active_dedupe_key=NULL`，再插入新单。

**问题2（`decide`）**：之前是"读一次审批单、在 Python 里判断、逐个字段赋值、
最后 commit"，两个管理员并发点"批准"/"拒绝"同一条单子都可能读到
`status=pending`，都会走到写入，最后提交的那个悄悄覆盖先提交的那个——数据库
最终状态取决于谁的事务后提交，不是谁先点的，而且两条 `approval_{status}`
审计记录都会被写下来。

修复：改成条件 `UPDATE ... WHERE id=:id AND status='pending'`，受影响行数为
0 说明在读到 pending 之后、真正写之前已经被别的并发请求抢先决定过了，直接
抛 `Conflict`（HTTP 409），不覆盖。

**真实并发验证**（`tests/test_approval_service.py`，均用真正的
`asyncio.gather` 并发调用，不是顺序调两次）：
- `test_concurrent_request_only_creates_one_row`：两个并发请求申请同一个
  `(action, resource_type, resource_id)`，两边拿到的必须是同一条记录，数据库
  里最终只有一行——不是靠代码看着像对就假设它对。
- `test_concurrent_decide_only_one_wins`：两个人同时一个批准一个拒绝，只有
  一个成功，另一个必须被拒绝（具体报 `Conflict` 还是更早的 `InvalidInput`
  取决于两边真实的执行时序，两种都是"正确识别出自己没抢到、没有覆盖对方"的
  安全结果，测试不区分）；数据库最终状态必须跟胜出者返回的状态完全一致。

新迁移文件跑完之后用 `scripts/check_no_migration_drift.py` 确认 ORM 模型
（`Base.metadata`）和迁移链完全一致，没有漂移（脚本另外报了一批跟这次改动
无关的既有漂移——`_run_migrations()` 那份冻结列表里几个字段的 MySQL 列注释
没有同步进 ORM 模型的 `comment=`，是这台本机开发库历史遗留的，不影响这次
改动本身干净）。全量 873 个测试通过，`scripts/release_check.py` 全量跑通
（含 compileall + 全量测试 + 前端构建）。

## 26. 第四轮审计 P1：部门 Agent 路由结果不确定（2026-09-28）

### P1-7：同一部门可以有多个已发布 Agent，路由选哪个是未定义行为

系统之前没有任何东西阻止创建/发布多个 `department_code` 相同的部门 Agent。
`central_router._find_department_agent` 按 `department_code` 查所有
`lifecycle_status='published'` 的候选，SQL 里没有唯一约束、没有优先级字段、
也没有稳定排序——如果真的存在多条，选中哪一条纯粹是运气（取决于查询计划/
索引扫描顺序，不是数据库承诺的行为），可能出现"同一个部门有两个 Agent 抢着
回答，今天选 A 明天选 B"的诡异现象。

修复：`models/init_db.py::Agent` 新增 `department_publish_key` 列 + 唯一约束
（`migrations/versions/20260928_0002_agent_department_publish_unique.py`），
跟第25节的审批去重是同一个思路——MySQL 唯一索引允许多个 NULL 共存，只在
非 NULL 值之间强制唯一。这一列由 `service/agent_admin_service.py::
update_managed_agent` 维护：`agent_type='department'` 且最终状态是
`lifecycle_status='published'` 时等于 `department_code` 本身，其余任何状态
（draft/reviewing/retired）清空成 NULL；"最终状态"取的是这次请求改动后的
值，没改的字段兜底用 Agent 当前值，不是简单看请求里传没传。发布第二个同
`department_code` 的 Agent 时会撞唯一键，捕获 `IntegrityError` 转成清楚的
`InvalidInput`（"部门「xxx」已经有一个已发布的 Agent 了，请先把旧的退役再
发布这一个"），不是让用户看到一个数据库报错。约束生效后同一个部门永远最多
只有一条 published 记录，`_find_department_agent` 不再需要纠结"选哪个"，
因为根本不会存在多个候选。

迁移文件里加了一步防御性数据清洗：如果加约束之前已经存在同一
`department_code` 的多条 published 记录（本机开发库验证过当前是空的），
先把除了 id 最小的那条之外全部打回 draft，再加约束，让迁移在真实存量数据
上也能安全跑，不是纸面上假设"应该没事"。

**修复过程中一个值得记录的坑**：第一版异常处理在 `except IntegrityError`
里访问 `agent.department_code` 拼错误消息，实测直接从 `InvalidInput`（预期
行为）变成了 `sqlalchemy.exc.MissingGreenlet`——`await db.rollback()` 之后
这个 ORM 对象的属性被标记过期，再访问它的列属性会触发一次隐式懒加载查询，
但 `AsyncSession` 里这种"裸属性访问"没法正确 `await`，直接报错，不是"检查
失败"而是"访问本身就出错"。修复：在真正执行 UPDATE 之前，把报错要用到的
值先存成本地变量，`except` 块里只读这个本地变量，不再碰 `agent` 的任何列
属性。这是先跑测试撞见的真实报错，不是凭经验提前绕开的。

真实 MySQL 测试（`tests/test_agent_admin_service.py::
DepartmentPublishUniquenessTest`，同样不依赖"本机默认企业是不是这个测试
建的那个"，直接 patch `_get_default_organization_id`）：发布第二个同
department_code 的 Agent 报 `InvalidInput`；把第一个退役之后第二个能正常
发布（不是"同一个部门永远只能有一个"，是"同时只能有一个"）；两个不同
department_code 的 Agent 可以同时发布，互不影响。跑完之后用
`scripts/check_no_migration_drift.py` 确认没有引入新的模型/迁移漂移。
全量 875 个测试通过，`release_check.py` 全量跑通。

至此，第四轮审计报告里的全部 2 个 P0 + 5 个 P1（Agent/Skill 生命周期强制、
乐观锁完整化、最后一个 owner 并发保护、审批流程并发缺口、部门 Agent 路由
确定性）全部完成。剩余 P1（#4 审计账号彻底去 root、#8 Java HMAC 完整签名）
和全部 P2（测试资源清理、覆盖率、文档同步）见文档末尾"还没做"部分。

## 27. 第四轮审计 P1：业务服务账号彻底去 root（2026-09-28）

### P1-4（后半部分）：主应用和 Java 服务处理业务请求时不再用 root

第21节/第23节已经做了审计表账号最小权限，但审计报告指出这只是一半——"主应用
和 Java 服务仍持有 MySQL root 密码"，处理真实业务请求（增删改查）那条路径
本身还是用 root 连库，一个被攻破的应用进程理论上能改表结构、建新账号、拿到
`mysql.user` 之类的系统表，权限范围跟它实际需要的完全不对等。

**设计**：业务运行时账号（Python 侧 `DB_USER`/`DB_PASSWORD`，Java 侧
`ENTERPRISE_DB_USER`/`ENTERPRISE_DB_PASSWORD`）只授予对应库的
`SELECT`/`INSERT`/`UPDATE`/`DELETE`，没有 `CREATE`/`ALTER`/`DROP`/`GRANT`；
真正需要建表权限的操作单独用一个新引入的、彻底独立的 `MYSQL_ROOT_PASSWORD`
（不再跟 `DB_PASSWORD` 复用同一个值——之前 `MYSQL_ROOT_PASSWORD` 直接读
`DB_PASSWORD`，业务账号一泄露等于 root 也泄露了，这个漏洞本身也顺手堵上）：

- **Python**：`deploy/mysql-init/03-create-app-runtime-users.sh` 建号（`GRANT ...
  ON db.*` 是整库通配，不要求表已存在，能跟 `CREATE USER` 放在同一步，不像
  审计账号那样要拆成"建号"和"补授权"两个阶段——这一点在真实 Docker 容器里
  验证过）。`DB_AUTO_BOOTSTRAP=0` 关掉进程内自动建表/幂等迁移，表结构完全
  交给 `alembic upgrade head` 管，运行时账号因此可以放心不给 DDL。迁移作为
  一次性手动命令，显式覆盖成 root（`docker compose run --rm -e DB_USER=root
  -e DB_PASSWORD="$MYSQL_ROOT_PASSWORD" api python -m alembic upgrade head`），
  长期运行的 `api`/`worker` 容器全程只读取限权账号，root 密码从不出现在它们
  的正常运行环境里。
- **Java**：`enterprise-business-hub` 的 Flyway 是 Spring Boot 约定在容器每次
  启动时自动跑的，没法像 Python 那样拆成完全独立的一次性命令——`application.yml`
  新增 `spring.flyway.url/user/password`，跟主 `spring.datasource.*`（JPA/
  Hibernate/JdbcTemplate，处理真实业务请求那条路径）分开配置：迁移阶段用
  `FLYWAY_DB_USER=root` 建表，建完之后所有业务请求处理都走
  `ENTERPRISE_DB_USER=app_runtime_java`（限权）。这意味着 Java 进程的环境变量
  里整个生命周期都会有 root 密码（不像 Python 那样彻底不出现）——这是一个
  已知的、比 Python 弱一点的折衷：SQL 注入类攻击（走应用自己的查询连接池）
  被限权账号完全挡住，但如果攻击者拿到了任意代码执行（能读进程环境变量），
  仍然能读到 root 密码。真正做到"迁移完全独立、进程里从不出现 root"需要把
  Flyway 迁移拆成单独的部署步骤（类似 K8s initContainer），这次不做这个更大的
  改动，评估后判断这个折衷在当前阶段可以接受。
- `service/config_validation.py` 新增生产环境校验：`MYSQL_ROOT_PASSWORD`
  必须显式设置、不能是占位值、不能跟 `DB_PASSWORD`/`ENTERPRISE_DB_PASSWORD`
  相同——这是最容易犯的配置错误（图省事把两个密码设成一样的，等于限权账号
  形同虚设），启动时直接拦截，不用等真出事才发现。

**真实 Docker 验证**（不是只看配置文件"看着像对"）：起了一个真实 MySQL 8
容器，把 `deploy/mysql-init/` 目录原样挂载进去（模拟 `docker-compose.prod.yml`
真实的初始化流程），确认三个脚本（`01`/`02`/`03`）都成功跑完、建号符合预期；
用 root 覆盖跑 `alembic upgrade head` 从空库建出完整 38 张表的 schema；用
`app_runtime` 账号验证 `INSERT`/`SELECT`/`DELETE` 成功、`ALTER TABLE` 被拒绝
（`ERROR 1142`）；Java 侧用 `FLYWAY_DB_USER=root` + `ENTERPRISE_DB_USER=
app_runtime_java` 跑通全部 35 个测试（含真实 HTTP + 真实 MySQL），同时验证
`app_runtime_java` 账号同样能 DML、不能 DDL。过程中撞见一次真实的操作失误
（忘记在跑 Java 测试前先执行审计账号的补授权脚本，报了一次
`Access denied for user 'audit_writer'@'%' to database 'enterprise_business'`
——这不是代码 bug，是部署顺序没走对，补跑授权脚本后立刻恢复正常，记录下来
是因为这正是文档里强调"迁移和授权必须按顺序执行"的真实原因，不是纸面上的
提醒）。

`.env.production.example`/`docker-compose.prod.yml`/`docs/deployment.md`
（新增 2.2 节）同步更新，含老部署的升级步骤。全量 875 个 Python 测试 +
新增的 `config_validation` 相关测试通过，`release_check.py` 全量跑通，Java
35 个测试通过。

### 还没做

Java 侧 Flyway 迁移和业务请求处理仍在同一个进程生命周期内，root 密码整个
进程运行期间都在环境变量里（只是不再被业务查询连接池使用）——彻底解决需要
把迁移拆成独立的部署步骤（类似 K8s initContainer 或单独的 migrate-once 容器），
评估后判断当前阶段这个折衷可以接受，不在这次一并做。

审计报告里提到的"审计采用事务 Outbox，重要审计同步到独立数据库或 OSS 保留"
也没有做——这是比账号最小权限更大的架构改动（需要引入消息队列或 WAL 式的
写入保证"业务成功但审计写入失败"不会发生），评估后判断超出这一轮的合理范围，
留作后续单独评估。

## 28. 第四轮审计 P1：Java 集成请求没有完整绑定到操作内容（2026-09-28）

### P1-8：HMAC 只签 X-Context，幂等处理"先查再执行"

两个独立的并发/完整性缺口，都在 FastAPI ↔ Java 企业业务中心这条签名 HTTP 调用链上。

**问题1：签名不覆盖方法/URL/请求体**。`HmacSignatureVerifier` 之前只对
`X-Context`（base64 后的 JSON）本身算 HMAC，不包含 HTTP 方法、URL、请求体；
`RequestContext` 里虽然带着 `operation` 字段，但从没有代码真的拿它跟实际调用
的接口做匹配（纯摆设）。截获一份合法的 `X-Context`/`X-Signature` 之后，
理论上能在到达服务端前换个方法/路径打过去，或者直接替换请求体，只要签名
本身没变、scope 恰好满足目标接口就能蒙混过关——不需要知道共享密钥。

修复：`RequestContext` 新增 `method`/`path`/`body_sha256` 三个字段，FastAPI
签发时把这次请求真正的方法、路径（含 query string）、请求体 SHA-256 都签
进 `X-Context`（整个 JSON 都在 HMAC 覆盖范围内，这三个字段也就跟着被保护）。
Java 侧 `SignedRequestContextFilter` 收到请求后，逐项核对这三个字段是不是
跟真实收到的请求一致，对不上直接 401。

请求体核对要求 Java 侧能在 Controller 反序列化之前先读一遍原始字节算哈希，
读完还要让下游 `@RequestBody` 正常拿到——原生 `HttpServletRequest` 的输入流
只能读一次，新增 `CachedBodyHttpServletRequest`（构造时整个缓存进内存，
`getInputStream()`/`getReader()` 每次调用都返回基于缓存的新流）解决。

FastAPI 侧 `service/enterprise_hub_client.py::call()` 必须自己把请求体序列化
成确定的字节串，再用这份完全一样的字节串去算哈希、签名、发送——不能用
`requests` 的 `json=` 参数让它自己再序列化一遍（哪怕语义相同，字段顺序不
保证一样，body_sha256 就会跟 Java 侧重新算的对不上，所有带请求体的合法请求
都会被拒）。改成手动 `json.dumps(...)` 一次、算哈希、`data=` 原样发送。

**问题2：幂等处理"先查再执行"**，跟第25节审批去重是同一类并发缺口。
`IdempotencyService.execute` 之前是"查有没有记录→没有就执行→执行完再插入"，
两个并发请求可能都查到"没有"，都各自执行一遍——外部 SAP/CRM 调用因此可能
被打两次。修复：改成"抢占式插入占位记录"，`idempotency_record` 新增
`completed` 列（迁移 `V4__idempotency_completed_flag.sql`）区分"占位"和
"已完成"；请求一进来先插一行 `completed=false` 的占位记录，主键唯一约束
保证两个并发请求只有一个能插入成功，插不进去的直接返回 409（"重复提交，
稍后重试"），不会跟着往下执行业务逻辑——不做"阻塞轮询等对方结果"（那需要
独立事务+轮询+超时，复杂度换来的只是极短时间内的体验优化，不是正确性
问题，正确性已经靠抢占式插入保证了）。

**真实验证**（不是只改代码就当作修好）：
- Python：`tests/test_enterprise_hub_client.py::CallSignsRealRequestBytesTest`
  直接 mock `requests.request`，拦下真正要发出去的 `data=` 字节，反过来验证
  `X-Context` 里签的 `body_sha256` 跟这份字节完全一致，不是另外算的一份。
- Java：`Leave`/`Procurement`/`Crm` 三个 `ControllerIntegrationTest` 各自新增
  `tamperedBodyAfterSigning_returns401`（签完名之后换请求体，必须被拒绝，
  用数据库状态确认没有被当成合法请求处理，不只看 HTTP 状态码——POST+401
  会撞一个已知的 JDK `HttpURLConnection` 限制，`ResourceAccessException` 也
  算通过，见测试内注释）和 `LeaveControllerIntegrationTest::
  replayingSignatureAgainstDifferentPath_returns401`（对 A 接口签的有效签名
  拿去打 B 接口，即使 scope 恰好也满足，必须因为 method/path 不匹配被拒绝）。
  这次改动同时要求全部 66 处已有的 `signedHeaders` 测试调用点改造成传入
  真实的 method/path/body（之前测试头是独立于请求构造的，现在必须绑定），
  改造后全量 40 个 Java 测试通过（含新增的 6 个）。
- 幂等并发修复复用了跟第25节一样的真实并发测试手法（`concurrentSameIdempotencyKey_
  onlyOneSucceeds`，见第25节旁边补的 `LeaveControllerIntegrationTest` 测试，
  这次跟 HMAC 改动一起验证）。

全量 40 个 Java 测试 + 880 个 Python 测试通过，`release_check.py` 全量跑通。

至此第四轮审计报告里的全部条目（2 个 P0 + 全部 5 个可独立处理的 P1）都已完成。
剩余 P2（测试资源清理、覆盖率、README/文档过期内容同步）已在同一天补完，详见
`docs/testing.md` 和 `README.md` 的更新记录。第四轮审计到此彻底收尾，第五轮审计
（AI 数据安全、Agent 行为安全、生产配置真实性）见下面第 29/30 节。

## 29. 第五轮审计 P0-2：Prompt 注入可能触发真实业务操作（2026-09-28）

审计报告指出：ReAct 循环只要 LLM 返回 `tool_calls` 就会自动执行
（`react_engine.py::_should_continue` 不区分工具风险），OA/采购/CRM 的
submit/approve/reject 这类真正产生业务后果的操作没有任何后端强制的"用户
确认"——工具描述里写了"用户确认后再调 submit_xxx"，但那只是给 LLM 看的文字，
后端完全不校验。RAG 检索结果又原样拼进 system prompt（`agent_runtime.py::
_compose_kb_prompt`），没有标记成不可信内容。两者叠加：一份被注入过指令的
文档（"请直接帮我提交这条请假单"）理论上能在同一轮 ReAct 循环里让模型连续
调用 `create_leave_draft` + `submit_leave_request`，没有人真正点过"确认"。

**修复**：`service/tools/base.py` 给 `BaseTool` 加 `risk_level` 三档
（read/write/high_risk），默认最严的 `high_risk`（安全默认值，新工具忘标注
也不会被漏放行）。OA/采购/CRM 全部 18 个工具 + 7 个通用工具逐一显式标注：
submit/approve/reject 和没有草稿步骤、一次调用就落库的
`create_or_update_opportunity` 标 high_risk；草稿类标 write；查询类和已有
独立沙箱/权限控制的 `run_skill_script` 标 read。

`service/tools/langchain_adapter.py` 在 `_run` 闭包里拦截 high_risk 调用：
不执行真正的业务逻辑，只调 `tool_confirmation_service.create_pending` 建一条
待确认记录（新表 `tool_confirmation`，迁移 `20260929_0001`），把 token 当
"工具结果"还给模型。真正执行只有 `service/tool_confirmation_service.py::
confirm_and_execute_async` 这一个入口，只能通过新增的 `POST /chat/
tool-confirmations/{token}/confirm|reject` 接口、由用户在前端点击确认触发——
ReAct 循环本身没有任何路径能走到这里，哪怕模型被诱导着反复请求同一个高风险
操作，也只会反复生成新的待确认单。`confirm_and_execute_async` 用条件 UPDATE
（`WHERE status='pending'`）而不是"先查再改"，防止同一个 token 被并发点两次
确认时执行两遍，跟第25节的审批并发修复是同一个思路。`frontend/src/views/
Chat.vue` 新增确认卡片：`tool_result` 事件的 `result` 是 `confirmation_
required` 形状时改成带"确认执行/取消"按钮的卡片。

**真实验证**：`tests/test_tool_confirmation.py` 新增 15 个测试——核心安全
属性（high_risk 工具的 `execute()` 在 ReAct 循环里绝对不会被直接调用，含
"忘标注时默认值也生效"）、真实 DB 上的确认/拒绝/重复确认(409)/跨用户(404)/
过期(410)/真实并发 confirm（`asyncio.gather`，只有一个成功）、真实路由级
测试（含未登录 401）。还用真实浏览器 + 真实 JWT + 真实待确认单验证过实际
接口：confirm 返回 200 并真的触达到工具执行（连不上 Java hub 时报连接失败，
不是权限或路由问题），重复 confirm 返回 409。顺手修了 `tests/_route_client.py::
_purge_users` 漏删 `tool_confirmation` 的 bug（真实撞见：第一轮测试后
cleanup 因为外键约束静默失败，导致用户名截断后的第二轮测试撞了唯一键）。
全量 895 个测试通过（880 基线 + 新增 15），`release_check.py` 全量跑通。

## 30. 第五轮审计 P1-4：审计账号缺失时不会按文档描述正确回退（2026-09-29）

审计报告指出两个问题：

**问题1**：`docker-compose.prod.yml` 给 `AUDIT_DB_PASSWORD` 写的是
`${AUDIT_DB_PASSWORD:-}`——`.env` 没设时，容器里这个环境变量会被设成空
字符串，不是"完全不存在"。`models/audit_db.py` 原来写的是
`os.getenv("AUDIT_DB_USER", DB_USER)`，这是两参数版本的 `os.getenv`，只有
变量真的不存在时才会用 default，变量存在但是空字符串会原样返回空字符串——
所以之前的"审计账号缺失时退回主账号"这句话在这种具体场景下不成立：实际
效果是拿着一个空密码去连一个不存在的账号，`service/audit_service.py` 的
broad except 把这个连接失败吞掉，业务照常成功，但没有任何审计记录，且没有
任何报错提示——生产环境完全有可能在这种"无审计静默失效"的状态下跑很久都
不会被发现。

**问题2**：`service/config_validation.py` 之前完全没有校验
`AUDIT_DB_USER`/`AUDIT_DB_PASSWORD`/`ENTERPRISE_HUB_HMAC_SECRET`/
`ENTERPRISE_DB_USER`/`ENTERPRISE_DB_PASSWORD`/`DB_AUTO_BOOTSTRAP`
这几项——它们哪怕留空/用默认值，应用都能正常启动，不会报错，只是悄悄降级
（审计账号退回主账号、HMAC 签名密钥缺失、DB_AUTO_BOOTSTRAP 没关掉导致进程内
自动建表和 alembic 迁移打架）。这类"不报错但悄悄降级"的配置问题最危险，
必须在生产环境启动时就拦下来，不能指望运维凭经验记住每一条。

**修复**：
- `models/audit_db.py`：`os.getenv(key, default)` 改成 `os.getenv(key) or
  default`——语义变成"空字符串也算没设"，跟"完全不存在"一视同仁。
- `service/config_validation.py` 的 `validate_runtime_config()` 在
  `production` 分支新增一组校验：`AUDIT_DB_USER`/`AUDIT_DB_PASSWORD` 必须
  都配、且 `AUDIT_DB_USER` 不能跟 `DB_USER` 相同（否则等于没有独立账号）；
  `ENTERPRISE_HUB_HMAC_SECRET`/`ENTERPRISE_DB_USER`/`ENTERPRISE_DB_PASSWORD`
  必须配；`DB_USER`/`AUDIT_DB_USER`/`ENTERPRISE_DB_USER` 都不能是 `root`；
  `DB_AUTO_BOOTSTRAP` 必须显式设为 `0`（语义跟 `models/init_db.py::
  _env_bool` 保持一致）。`assert_runtime_config()` 在生产环境遇到任何一条
  失败就直接拒绝启动，跟已有的 `MYSQL_ROOT_PASSWORD`/`ADMIN_PASSWORD` 等
  校验走的是同一条路径。
- `docker-compose.prod.yml` 里 `AUDIT_DB_USER` 的注释从"可选，不设就退回
  DB_USER 写审计"改成"生产环境必填，不设会被启动校验直接拒绝"，跟实际行为
  保持一致。

**真实验证**：`tests/test_audit_db_fallback.py` 新增 2 个测试，用
`importlib.reload` 在补丁过的环境变量下重新执行 `models/audit_db.py` 的
模块顶层代码——先在旧代码上跑一遍确认测试真的会失败（`AUDIT_DB_USER` 被
设成空字符串时解出来是 `''` 而不是退回的 `DB_USER`，实测复现了这个 bug），
改完代码后再跑一遍确认变绿，不是凭经验直接判断"应该修好了"。
`tests/test_config_validation.py` 新增 8 个测试覆盖新增的六项校验（缺失、
跟 DB_USER 重复、占位符、root 用户名、DB_AUTO_BOOTSTRAP 各种取值），并把
`_valid_env()` 基线补上这些新必填项，同步修好了它引发的几个既有测试。
全量 905 个测试通过（895 基线 + 新增 10），ruff/compileall 干净，
`release_check.py` 全量跑通。

## 31. 第五轮审计 P1-5：审批被消费后，实际业务操作仍可能失败（2026-09-29）

审计报告指出：`approval_service.try_consume_approved` 先把审批单标
`executed_at`（认领）并自己 `commit`，之后调用方才真正执行业务操作（比如
`space_async_service.delete_space` 删知识空间）。这两步分属两个独立事务：
如果真正执行那一步失败（DB 故障、进程崩溃），审批已经被标记消费、且已经
落库，但业务没有成功；而且 `active_dedupe_key` 只在 `rejected` 时释放，
`approved→consumed` 之后不会清空——旧审批用不了（已消费），新审批又申请不了
（dedupe key 还占着同一个 `(action, resource_type, resource_id)`），资源
卡死在一个既没删成又申请不了新审批的状态。

**修复**：`try_consume_approved` 不再自己 `commit`，只做那条条件 UPDATE
（原子性不受影响，仍然靠 UPDATE 语句本身的行锁保证）；调用方必须让"标记
已消费"和"真正执行业务操作"落在同一个未提交事务里，最后由业务操作自己的
commit 一起提交。`space_async_service.delete_space` 因此不需要显式改
commit 逻辑——它本来就靠 `knowledge_space_async_dao.delete_space_async`
最后那一次 `db.commit()` 收尾，只要中间不再有别的地方提前 commit 就自动
获得了这个原子性。

**修复过程中一个值得记录的坑**：第一版只改了 `try_consume_approved`，验证
时发现审批照样在业务操作失败前就被永久标记消费——排查发现
`delete_space` 在真正删除**之前**调用的 `_audit(...)`（写 `KbAuditLog`）
底层是 `kb_audit_dao.record_async`，这个函数自己会 `db.commit()`。之前这个
提前 commit 不是问题（因为 `try_consume_approved` 反正也会自己提前
commit，审计提交早一点晚一点没区别），但去掉 `try_consume_approved` 的
commit 之后，这条藏在审计逻辑里的 commit 变成了新的"提前冲掉未提交状态"
的点，把刚做的修复又绕开了。修法：把 `_audit(...)` 调用挪到真正删除、真正
commit 之后（审计记录的是"已经发生的事实"，本来就该在事实发生之后写，
审计本身失败也不影响主流程，`_audit` 内部的 try/except 兜底不变）。这个坑
提醒了一件事：某个函数"看起来没有副作用的提交行为"（这里是一条审计写入）
可能悄悄依赖着调用顺序，改动事务边界时不能只看直接相关的代码，要顺着调用链
把每一步会不会提交都查一遍，不能靠"看起来不会"就跳过。

**真实验证**：没有只看代码就假设修好了——写了真实场景复现：`patch` 掉
`knowledge_space_async_dao.delete_space_async` 让它抛异常，模拟"审批被
消费之后真正的删除失败"，先在只改了 `try_consume_approved`、还没挪 `_audit`
调用顺序的中间状态下跑了一遍，实测复现"重试仍然要求重新审批"（说明还是
卡死了），确认前面这个坑是真的会发生，不是理论推测；改完 `_audit` 调用
顺序后再跑一遍，确认空间在失败后还在、重试不需要重新审批直接就能删掉。
`tests/test_approval_service.py` 新增
`test_uncommitted_consume_can_be_retried`（`try_consume_approved` 之后
不 commit，session 关闭自动回滚，换一个新 session 能重新消费到同一条单）
和 `test_delete_failure_after_consume_can_be_retried`（真实场景，`patch`
真正删除函数抛异常，验证空间没被删、审批没被卡死、重试直接成功），并修了
`test_concurrent_consume_only_one_wins`——`try_consume_approved` 不再自己
commit 之后，测试里"赢家"必须自己补一次 commit 才算完整模拟了真实调用方
（消费+真正业务操作一起提交），不然两个并发协程都会在对方回滚后重新抢到
同一条审批单，反而破坏了原来要测的"只有一个赢家"这个断言。全量 907 个
测试通过（905 基线 + 新增 2），ruff/compileall 干净，`release_check.py`
全量跑通。
