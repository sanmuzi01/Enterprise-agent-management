/** 当前用户现在就能调用的聊天模型：管理员已为全公司连接了它的服务商，或者用户自己配过它的密钥。
 * 让人手填模型名（例如 RAG 评估的“评分模型”）时，填了一个没接通的名字只会在运行时报错；改成从这里选。 */

export interface CatalogModel {
  model_name: string
  provider: string
  kind?: string
}

export interface UsableModel {
  model_name: string
  provider: string
  /** 密钥来源：管理员统一连接 / 自己配置 */
  source: 'enterprise' | 'personal'
}

export function usableChatModels(
  catalog: CatalogModel[],
  enterpriseProviders: string[],
  personalModelNames: string[],
): UsableModel[] {
  const enterprise = new Set(enterpriseProviders.map((p) => p.toLowerCase()))
  const personal = new Set(personalModelNames.map((m) => m.trim().toLowerCase()))
  const seen = new Set<string>()
  const result: UsableModel[] = []
  for (const model of catalog) {
    if (model.kind && model.kind !== 'chat') continue
    const key = model.model_name.toLowerCase()
    if (seen.has(key)) continue
    // 用户自己的密钥优先（聊天时也是先用个人配置）
    const source = personal.has(key) ? 'personal' : enterprise.has(model.provider.toLowerCase()) ? 'enterprise' : null
    if (!source) continue
    seen.add(key)
    result.push({ model_name: model.model_name, provider: model.provider, source })
  }
  return result
}
