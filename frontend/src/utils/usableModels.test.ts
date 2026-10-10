import { describe, expect, it } from 'vitest'
import { usableChatModels } from './usableModels'

const catalog = [
  { model_name: 'glm-4', provider: 'zhipu', kind: 'chat' },
  { model_name: 'glm-4-flash', provider: 'zhipu', kind: 'chat' },
  { model_name: 'gpt-4o-mini', provider: 'openai', kind: 'chat' },
  { model_name: 'deepseek-chat', provider: 'deepseek', kind: 'chat' },
  { model_name: 'embedding-3', provider: 'zhipu', kind: 'embedding' },
]

describe('usableChatModels', () => {
  it('只列管理员已连接的服务商的聊天模型，不列向量模型', () => {
    expect(usableChatModels(catalog, ['zhipu'], []).map((m) => m.model_name)).toEqual(['glm-4', 'glm-4-flash'])
  })

  it('用户自己配了密钥的模型也能选，并标明来源', () => {
    const models = usableChatModels(catalog, ['zhipu'], ['GPT-4o-mini'])
    expect(models.find((m) => m.model_name === 'gpt-4o-mini')?.source).toBe('personal')
    expect(models.find((m) => m.model_name === 'glm-4')?.source).toBe('enterprise')
  })

  it('哪儿都没接通：一个也不列（页面提示去连接模型），不让人填一个注定报错的名字', () => {
    expect(usableChatModels(catalog, [], [])).toEqual([])
  })
})
