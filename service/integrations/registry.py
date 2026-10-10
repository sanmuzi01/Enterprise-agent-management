"""provider → 适配器。新增平台（企业微信等）只需要实现 ProviderAdapter 并在这里登记。"""
from service.integrations.apps import check_provider
from service.integrations.base import ProviderAdapter


def get_adapter(provider: str) -> ProviderAdapter:
    check_provider(provider)
    if provider == "feishu":
        from service.integrations.feishu.adapter import FeishuAdapter
        return FeishuAdapter()
    from service.integrations.dingtalk.adapter import DingTalkAdapter
    return DingTalkAdapter()
