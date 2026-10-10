<template>
  <div class="p-6">
    <p class="mb-4 max-w-3xl text-xs leading-relaxed text-slate-500">
      报销时按这里的标准自动检查：超过上限的必须填写超标准说明（写进报销明细，审批人能看到），规定要发票的没有发票不能提交。
      条件为空表示不限；同一类费用有多条时，城市、职级都指定的那条优先。
    </p>
    <p v-if="error" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{{ error }}</p>

    <form class="mb-5 grid gap-2 rounded-lg border border-slate-200 bg-white p-4 text-sm sm:grid-cols-4" data-testid="policy-form" @submit.prevent="save">
      <div>
        <label for="pol-category" class="block text-xs text-slate-500">费用类别</label>
        <select id="pol-category" v-model="form.category" class="mt-1 h-9 w-full rounded border border-slate-300 px-2">
          <option v-for="(label, key) in meta.categories" :key="key" :value="key">{{ label }}</option>
        </select>
      </div>
      <div>
        <label for="pol-city" class="block text-xs text-slate-500">城市等级</label>
        <select id="pol-city" v-model="form.city_level" class="mt-1 h-9 w-full rounded border border-slate-300 px-2">
          <option :value="null">不限</option>
          <option v-for="(label, key) in meta.city_levels" :key="key" :value="key">{{ label }}</option>
        </select>
      </div>
      <div>
        <label for="pol-level" class="block text-xs text-slate-500">职级</label>
        <select id="pol-level" v-model="form.employee_level" class="mt-1 h-9 w-full rounded border border-slate-300 px-2">
          <option :value="null">不限</option>
          <option v-for="(label, key) in meta.employee_levels" :key="key" :value="key">{{ label }}</option>
        </select>
      </div>
      <div>
        <label for="pol-limit" class="block text-xs text-slate-500">单笔上限（元，空 = 不限额）</label>
        <input id="pol-limit" v-model="form.amount_limit" inputmode="decimal" class="mt-1 h-9 w-full rounded border border-slate-300 px-2" />
      </div>
      <div>
        <label for="pol-approval" class="block text-xs text-slate-500">超标后审批</label>
        <select id="pol-approval" v-model="form.approval_level" class="mt-1 h-9 w-full rounded border border-slate-300 px-2">
          <option v-for="(label, key) in meta.approval_levels" :key="key" :value="key">{{ label }}</option>
        </select>
      </div>
      <div>
        <label for="pol-from" class="block text-xs text-slate-500">生效日期</label>
        <input id="pol-from" v-model="form.effective_from" type="date" class="mt-1 h-9 w-full rounded border border-slate-300 px-2" />
      </div>
      <div>
        <label for="pol-to" class="block text-xs text-slate-500">截止日期</label>
        <input id="pol-to" v-model="form.effective_to" type="date" class="mt-1 h-9 w-full rounded border border-slate-300 px-2" />
      </div>
      <label class="flex items-end gap-2 pb-2 text-sm text-slate-700">
        <input id="pol-receipt" v-model="form.receipt_required" type="checkbox" class="h-4 w-4" />必须有发票
      </label>
      <div class="sm:col-span-3">
        <label for="pol-note" class="block text-xs text-slate-500">说明（可选）</label>
        <input id="pol-note" v-model="form.note" maxlength="300" class="mt-1 h-9 w-full rounded border border-slate-300 px-2" />
      </div>
      <div class="flex items-end gap-2">
        <button type="submit" :disabled="busy" data-testid="policy-save" class="h-9 rounded bg-indigo-600 px-4 text-white hover:bg-indigo-700 disabled:opacity-50">{{ editing ? '保存修改' : '新增标准' }}</button>
        <button v-if="editing" type="button" @click="reset" class="h-9 rounded px-3 text-slate-600 hover:bg-slate-100">取消</button>
      </div>
    </form>

    <div class="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table class="w-full min-w-[720px] text-left text-sm" data-testid="policy-table">
        <thead class="text-xs text-slate-500">
          <tr class="border-b border-slate-100">
            <th class="px-3 py-2 font-medium">类别</th><th class="px-3 py-2 font-medium">城市</th><th class="px-3 py-2 font-medium">职级</th>
            <th class="px-3 py-2 font-medium">单笔上限</th><th class="px-3 py-2 font-medium">发票</th><th class="px-3 py-2 font-medium">超标审批</th>
            <th class="px-3 py-2 font-medium">生效期</th><th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rules" :key="r.id" class="border-b border-slate-50">
            <td class="px-3 py-2">{{ r.category_label }}</td>
            <td class="px-3 py-2">{{ r.city_level ? meta.city_levels[r.city_level] : '不限' }}</td>
            <td class="px-3 py-2">{{ r.employee_level ? meta.employee_levels[r.employee_level] : '不限' }}</td>
            <td class="px-3 py-2 tabular-nums">{{ r.amount_limit ? `¥${r.amount_limit}` : '不限额' }}</td>
            <td class="px-3 py-2">{{ r.receipt_required ? '必须' : '可以没有' }}</td>
            <td class="px-3 py-2">{{ r.approval_label }}</td>
            <td class="px-3 py-2 text-xs text-slate-500">{{ r.effective_from || '一直' }} ～ {{ r.effective_to || '长期' }}</td>
            <td class="px-3 py-2 text-right text-xs">
              <button @click="edit(r)" class="rounded px-2 py-1 text-indigo-700 hover:bg-indigo-50">修改</button>
              <button @click="remove(r)" class="rounded px-2 py-1 text-red-600 hover:bg-red-50">删除</button>
            </td>
          </tr>
          <tr v-if="!rules.length"><td colspan="8" class="px-3 py-8 text-center text-xs text-slate-400">还没有费用标准。没有标准时报销不做额度检查。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import * as api from '../../api/financeExtras'
import type { PolicyRule, PolicyRuleForm } from '../../api/financeExtras'
import { getErrorMessage } from '../../utils/request'
import { toastSuccess } from '../../utils/toast'

const rules = ref<PolicyRule[]>([])
const meta = reactive({ categories: {} as Record<string, string>, city_levels: {} as Record<string, string>,
  employee_levels: {} as Record<string, string>, approval_levels: {} as Record<string, string> })
const error = ref('')
const busy = ref(false)
const editing = ref<number | null>(null)
const blank = (): PolicyRuleForm => ({ category: 'MEAL', city_level: null, employee_level: null, amount_limit: '',
  receipt_required: true, approval_level: 'team_admin', effective_from: null, effective_to: null, note: '' })
const form = reactive<PolicyRuleForm>(blank())

async function load() {
  error.value = ''
  try {
    const data = await api.listPolicies()
    rules.value = data.rules
    Object.assign(meta, data)
  } catch (e) {
    error.value = getErrorMessage(e, '加载失败')
  }
}

function reset() {
  Object.assign(form, blank())
  editing.value = null
}

function edit(r: PolicyRule) {
  editing.value = r.id
  Object.assign(form, { category: r.category, city_level: r.city_level, employee_level: r.employee_level,
    amount_limit: r.amount_limit || '', receipt_required: r.receipt_required, approval_level: r.approval_level,
    effective_from: r.effective_from, effective_to: r.effective_to, note: r.note || '' })
}

async function save() {
  busy.value = true
  error.value = ''
  try {
    await api.savePolicy({ ...form, amount_limit: form.amount_limit || null, effective_from: form.effective_from || null,
      effective_to: form.effective_to || null, note: form.note || null }, editing.value ?? undefined)
    toastSuccess('已保存')
    reset()
    await load()
  } catch (e) {
    error.value = getErrorMessage(e, '保存失败')
  } finally {
    busy.value = false
  }
}

async function remove(r: PolicyRule) {
  if (!window.confirm(`删除“${r.category_label}”的这条费用标准？`)) return
  try {
    await api.deletePolicy(r.id)
    await load()
  } catch (e) {
    error.value = getErrorMessage(e, '删除失败')
  }
}

onMounted(load)
</script>
