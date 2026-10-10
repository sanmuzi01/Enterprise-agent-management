package com.enterprisehub.hr;

import java.util.List;

/** 人事事项类型及其办理清单模板：(标题, 办理方, 是否必办, 相对生效日的天数)。 */
public enum HrCaseType {
    ONBOARDING("入职", List.of(
            new Tpl("签订劳动合同", "HR", true, -1),
            new Tpl("办理入职登记并建立人事档案", "HR", true, 0),
            new Tpl("开通账号和邮箱", "IT", true, -1),
            new Tpl("配发电脑等办公设备", "IT", false, 0),
            new Tpl("登记工资卡和报销账户", "FINANCE", true, 3),
            new Tpl("指定导师并安排入职培训", "MANAGER", true, 0),
            new Tpl("阅读并确认公司制度", "EMPLOYEE", true, 3))),
    PROBATION("转正", List.of(
            new Tpl("提交转正评价", "MANAGER", true, -5),
            new Tpl("核对试用期考勤与评价", "HR", true, -3),
            new Tpl("调整薪资并更新合同", "HR", true, 0),
            new Tpl("确认转正结果", "EMPLOYEE", false, 3))),
    TRANSFER("调岗", List.of(
            new Tpl("完成工作交接", "MANAGER", true, 0),
            new Tpl("调整系统权限和部门资源", "IT", true, 0),
            new Tpl("调整费用和预算归属", "FINANCE", false, 3),
            new Tpl("更新人事档案和岗位信息", "HR", true, 0))),
    OFFBOARDING("离职", List.of(
            new Tpl("完成工作交接", "MANAGER", true, -3),
            new Tpl("回收电脑等设备", "IT", true, 0),
            new Tpl("注销账号并回收权限", "IT", true, 0),
            new Tpl("结清借款与报销", "FINANCE", true, 0),
            new Tpl("出具离职证明并办理档案转移", "HR", true, 3)));

    public record Tpl(String title, String owner, boolean required, int dayOffset) {
    }

    private final String label;
    private final List<Tpl> tasks;

    HrCaseType(String label, List<Tpl> tasks) {
        this.label = label;
        this.tasks = tasks;
    }

    public String label() {
        return label;
    }

    public List<Tpl> tasks() {
        return tasks;
    }
}
