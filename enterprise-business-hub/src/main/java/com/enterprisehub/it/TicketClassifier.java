package com.enterprisehub.it;

import com.enterprisehub.it.dto.ItDtos.Classification;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

/**
 * 工单分类与优先级建议：全是关键词规则，结果带依据，只是“建议”——申请人/AI 填的分类不会被它覆盖，
 * 对不上时提示出来，IT 人员可以改。账号开通/变更、权限开通、设备申请需要先审批；
 * 忘记密码、账号被锁属于故障（自助或 IT 直接处理，不走审批）。
 */
@Component
public class TicketClassifier {
    private static final List<String> DEVICE_NOUNS = List.of("笔记本", "电脑", "显示器", "键盘", "鼠标", "手机", "耳机", "设备", "台式机", "主机");
    private static final List<String> DEVICE_VERBS = List.of("申请", "领用", "换新", "更换", "采购", "新配", "配一", "借用");
    private static final List<String> PERMISSION = List.of("权限", "授权", "开通访问", "加入群组", "共享盘", "文件夹访问");
    private static final List<String> ACCOUNT = List.of("开通账号", "新员工账号", "新账号", "注册账号", "开账号", "账号开通", "注销账号", "账号变更", "邮箱开通");
    private static final List<String> INCIDENT = List.of("故障", "无法", "打不开", "报错", "蓝屏", "崩溃", "连不上", "卡顿", "很慢",
            "打印机", "密码", "忘记", "锁定", "断网", "收不到", "发不出", "死机", "异常", "登不上", "不能用");
    private static final List<String> URGENT = List.of("所有人", "全公司", "全部门", "整层", "整个部门", "宕机", "生产环境", "无法办公", "全部无法", "大面积");
    private static final List<String> HIGH = List.of("影响工作", "马上", "今天", "客户", "会议", "紧急", "尽快", "着急", "急用", "无法开展");
    private static final List<String> LOW = List.of("咨询", "不急", "有空", "建议", "顺便", "想了解");

    public Classification classify(String text) {
        String value = text == null ? "" : text.toLowerCase();
        List<String> reasons = new ArrayList<>();
        TicketCategory category = TicketCategory.OTHER;

        String noun = firstHit(value, DEVICE_NOUNS);
        String verb = firstHit(value, DEVICE_VERBS);
        String account = firstHit(value, ACCOUNT);
        String permission = firstHit(value, PERMISSION);
        String incident = firstHit(value, INCIDENT);
        if (noun != null && verb != null && incident == null) {
            category = TicketCategory.DEVICE;
            reasons.add("同时出现“" + verb + "”和“" + noun + "” → 设备申请");
        } else if (account != null) {
            category = TicketCategory.ACCOUNT;
            reasons.add("出现“" + account + "” → 账号申请");
        } else if (permission != null && incident == null) {
            category = TicketCategory.PERMISSION;
            reasons.add("出现“" + permission + "” → 权限申请");
        } else if (incident != null) {
            category = TicketCategory.INCIDENT;
            reasons.add("出现“" + incident + "” → 故障");
        } else {
            reasons.add("没有命中分类关键词 → 咨询/其他");
        }

        TicketPriority priority = TicketPriority.NORMAL;
        String urgent = firstHit(value, URGENT);
        String high = firstHit(value, HIGH);
        String low = firstHit(value, LOW);
        if (urgent != null) {
            priority = TicketPriority.URGENT;
            reasons.add("出现“" + urgent + "”，影响范围大 → 紧急");
        } else if (high != null && category == TicketCategory.INCIDENT) {
            priority = TicketPriority.HIGH;
            reasons.add("出现“" + high + "”，影响当前工作 → 高");
        } else if (low != null) {
            priority = TicketPriority.LOW;
            reasons.add("出现“" + low + "” → 低");
        }
        return new Classification(category.name(), priority.name(), reasons);
    }

    private static String firstHit(String text, List<String> keywords) {
        return keywords.stream().filter(text::contains).findFirst().orElse(null);
    }
}
