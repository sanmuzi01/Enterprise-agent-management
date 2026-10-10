import request from '../utils/request'

export interface HealthCheckItem {
  name: string
  ok: boolean
  message: string
}

export interface HealthStatus {
  ok: boolean
  checks: HealthCheckItem[]
  cache?: {
    config?: {
      backend?: string
      redis_ok?: boolean
      size: number
      memory_size?: number
      redis_size?: number
      expired_removed: number
    }
    skill?: {
      backend?: string
      redis_ok?: boolean
      size: number
      memory_size?: number
      redis_size?: number
      expired_removed: number
    }
    verification?: {
      backend?: string
      redis_ok?: boolean
      size: number
      memory_size?: number
      redis_size?: number
      expired_removed: number
    }
  }
  limits?: {
    rate?: {
      backend?: string
      redis_ok?: boolean
      memory_size?: number
    }
    concurrency?: {
      backend?: string
      redis_ok?: boolean
      memory_active?: number
    }
  }
  tasks?: {
    execution_mode?: string
    worker_required?: boolean
    running_timeout_seconds?: number
    max_auto_retries?: number
    retry_base_seconds?: number
    retry_max_seconds?: number
  }
  database?: {
    pool?: Record<string, number | null>
  }
  config?: {
    environment?: string
    ok?: boolean
    error_count?: number
    warning_count?: number
    checks?: Array<HealthCheckItem & { level?: string }>
  }
  resilience?: {
    circuits?: Record<string, unknown>
  }
}

/** 完整运行诊断（DB 连接池 / 缓存 / 限流 / 熔断器等），需要登录。
 * 设置页、管理后台的诊断面板用这个。匿名探活见 `/health`（本项目没有对应的前端封装——
 * 那个端点是给不带登录态的基础设施探活用的，不是给页面调的）。*/
export async function getDiagnose(): Promise<HealthStatus> {
  const { data } = await request.get('/system/diagnose')
  return data as HealthStatus
}


export interface ReadinessItem {
  key: string
  label: string
  ok: boolean
  level: 'ok' | 'warn' | 'error'
  message: string
  fix: string
}

/** 运行就绪检查：数据库、迁移版本、企业业务服务、模型、提醒任务、演示数据；每项独立、带处理办法。 */
export async function getReadiness(): Promise<{ ok: boolean; checks: ReadinessItem[] }> {
  const { data } = await request.get('/system/readiness')
  return data
}
