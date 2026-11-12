/** 可访问的侧栏调宽控件：指针拖拽、方向键微调与双击复位。 */
import { useRef } from 'react';
import { clampWidth, WIDTHS } from './state';
import type { Side } from './state';

export default function ResizableSeparator({ side, width, onChange }: { side: Side; width: number; onChange: (width: number) => void }) {
  const dragging = useRef<{ x: number; width: number } | null>(null);
  const sign = side === 'left' ? 1 : -1;
  const label = side === 'left' ? '数据目录宽度' : '数据助手宽度';
  return <div
    className="resize-handle" role="separator" tabIndex={0} aria-label={label} aria-orientation="vertical"
    aria-valuenow={width} aria-valuemin={WIDTHS[side].min} aria-valuemax={WIDTHS[side].max}
    title="拖拽调宽；方向键微调；双击恢复默认"
    onDoubleClick={() => onChange(WIDTHS[side].default)}
    onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
      event.preventDefault();
      onChange(event.key === 'Home' ? WIDTHS[side].default : clampWidth(side, width + (event.key === 'ArrowRight' ? 8 : -8) * sign));
    }}
    onPointerDown={event => {
      dragging.current = { x: event.clientX, width };
      event.currentTarget.setPointerCapture(event.pointerId);
      event.preventDefault();
    }}
    onPointerMove={event => {
      if (dragging.current) onChange(clampWidth(side, dragging.current.width + (event.clientX - dragging.current.x) * sign));
    }}
    onPointerUp={event => { dragging.current = null; event.currentTarget.releasePointerCapture(event.pointerId); }}
    onPointerCancel={() => { dragging.current = null; }}
  ><span /></div>;
}
