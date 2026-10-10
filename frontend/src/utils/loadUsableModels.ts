import { listEnterpriseProviders } from '../api/adminLlm'
import { listConfigs, listSupportedModelCatalog } from '../api/llmConfig'
import { usableChatModels, type UsableModel } from './usableModels'

/** 当前用户现在就能调用的聊天模型（管理员统一连接的 + 自己配置的）。加载失败返回空列表。 */
export async function loadUsableChatModels(): Promise<UsableModel[]> {
  try {
    const [catalog, providers, configs] = await Promise.all([
      listSupportedModelCatalog(),
      listEnterpriseProviders().catch(() => []),
      listConfigs().catch(() => []),
    ])
    return usableChatModels(catalog.chat, providers.map((p) => p.provider), configs.map((c) => c.model_name))
  } catch {
    return []
  }
}
