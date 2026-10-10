/** 中文输入法正在选词时按下的回车（isComposing，或部分浏览器上 keyCode 229）是在确认候选词，不是“提交”。
 *
 * 回车提交的输入框都要先判断这一下：不判断的话，用拼音打字时按回车选词，会把还没打完的内容直接发出去。
 * 只能用 keydown 判断——Chrome 里选词之后的那次 keyup 已经不带 isComposing 了，用 keyup.enter 拦不住。
 */
export function isImeEnter(event: KeyboardEvent): boolean {
  return event.isComposing || event.keyCode === 229
}
