"""统一外部接入层：飞书、钉钉等协作平台接进来的消息、卡片按钮、组织架构，都先变成平台自己的结构再处理。

    收到外部消息 → 验签和解密 → 入站去重（external_event_inbox）→ 找本地用户 → 查部门 → 选中央 Agent
    → 调用同一个 Agent 运行时（service/chat_pipeline.py）→ 把回答和待确认卡片发回去

每个平台只需要实现 base.ProviderAdapter：验签解密、把回调转成 InboundMessage / CardAction、发消息、渲染卡片、
拉组织架构。新增平台在 registry.py 里登记即可，其余流程（身份、去重、Agent、确认）全部共用。
"""
