"""Agent/Skill 共用的发布生命周期常量（docs/enterprise-rbac-plan.md 相关记录）。

`draft -> reviewing -> published -> retired`，Agent 和 Skill 两张表的
`lifecycle_status` 列共用同一份合法值，不允许两边各自定义一份枚举——那样迟早会
出现两边校验对不上的情况（跟 `service/runtime/central_router.py` 里
`VALID_AGENT_TYPES`/`VALID_DEPARTMENT_CODES` 单独定义是因为那两个是 Agent 专属
概念，跟这里的道理不冲突）。

不做严格的状态机图校验（只允许 A->B 这种边）：单一管理员运营的模型下没必要，
任何合法值之间都能来回改，只校验"是不是这四个值之一"。
"""

VALID_LIFECYCLE_STATUSES = {"draft", "reviewing", "published", "retired"}

# 能被"非所有者"绑定/使用的状态——owner 自己测试草稿不受这个限制。
BINDABLE_BY_OTHERS_STATUSES = {"published"}

# 任何人都不能再使用的状态，即便是所有者自己的 Agent 也不行——退役就是彻底停用。
RETIRED_STATUS = "retired"
