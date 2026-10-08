<template>
  <article
    class="transition-all duration-500 ease-[var(--spring)] hover:-translate-y-0.5 hover:shadow-[var(--sh-2)] flex flex-col rounded-lg border border-sky-200 bg-white/80 p-4 shadow-sm transition"
    :class="{ 'opacity-60': space.status === 'archived' || !space.is_enabled }"
  >
    <div class="flex items-start justify-between gap-2">
      <div class="min-w-0">
        <p class="truncate text-sm font-semibold text-slate-900">{{ space.name }}</p>
        <p class="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-slate-400">
          <template v-if="space.scope === 'department'">
            <span v-for="d in space.departments" :key="d.id" class="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-600">{{ d.name }}</span>
          </template>
          <span v-if="space.scope === 'enterprise'" class="rounded bg-purple-50 px-1.5 py-0.5 text-purple-600">全企业</span>
          <span v-else-if="space.scope === 'shared'" class="rounded bg-violet-50 px-1.5 py-0.5 text-violet-600">别人分享给我</span>
          <span v-if="space.sensitivity && space.sensitivity !== 'internal'" class="rounded px-1.5 py-0.5" :class="sensitivityClass">{{ space.sensitivity_label }}</span>
          <span v-if="space.purpose_label" class="rounded bg-sky-50 px-1.5 py-0.5 text-sky-600">{{ space.purpose_label }}</span>
          <span v-for="t in space.tags" :key="t" class="rounded bg-slate-100 px-1.5 py-0.5">{{ t }}</span>
        </p>
      </div>
      <span
        class="mt-0.5 inline-block h-2 w-2 shrink-0 rounded-full"
        :class="healthClass"
        :title="healthTitle"
      ></span>
    </div>

    <p v-if="space.description" class="mt-2 line-clamp-2 text-xs text-slate-500">{{ space.description }}</p>

    <dl class="mt-3 grid grid-cols-3 gap-2 text-center">
      <div><dt class="text-[10px] text-slate-400">文档</dt><dd class="text-sm font-medium text-slate-800">{{ space.doc_count }}</dd></div>
      <div><dt class="text-[10px] text-slate-400">片段</dt><dd class="text-sm font-medium text-slate-800">{{ space.chunk_count }}</dd></div>
      <div><dt class="text-[10px] text-slate-400">绑定 Agent</dt><dd class="text-sm font-medium text-slate-800">{{ space.bound_agent_count ?? '—' }}</dd></div>
    </dl>

    <p class="mt-2 text-[11px] text-slate-400">
      {{ space.last_indexed_at ? '最近入库 ' + space.last_indexed_at : '还没有文档' }}
    </p>

    <div class="mt-3 flex gap-2 border-t border-sky-100 pt-3">
      <button class="flex-1 rounded border border-sky-200 px-2 py-1 text-xs text-slate-600 hover:bg-sky-50" @click="emit('open')">
        打开
      </button>
      <button v-if="space.can_manage !== false" class="rounded border border-sky-200 px-2 py-1 text-xs text-slate-600 hover:bg-sky-50" @click="emit('edit')">编辑</button>
      <button v-if="space.can_delete !== false" class="rounded border border-red-200 px-2 py-1 text-xs text-red-500 hover:bg-red-50" @click="emit('delete')">删除</button>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { KnowledgeSpace } from '../../api/knowledgeSpace'

const props = defineProps<{ space: KnowledgeSpace }>()
const emit = defineEmits<{ (e: 'open' | 'edit' | 'delete'): void }>()

const sensitivityClass = computed(() => {
  const level = props.space.sensitivity
  if (level === 'restricted') return 'bg-red-50 text-red-600'
  if (level === 'confidential') return 'bg-amber-50 text-amber-600'
  return 'bg-emerald-50 text-emerald-600'
})

const healthClass = computed(() => {
  const s = props.space.health_score
  if (s == null) return 'bg-slate-300'
  if (s >= 80) return 'bg-emerald-500'
  if (s >= 50) return 'bg-amber-500'
  return 'bg-red-500'
})
const healthTitle = computed(() =>
  props.space.health_score == null ? '健康分待评估' : `健康分 ${props.space.health_score}`,
)
</script>
