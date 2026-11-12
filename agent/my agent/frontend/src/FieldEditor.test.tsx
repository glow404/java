/** 字段别名编辑测试：别名独立保存，保持逻辑字段标识不变并记录用户来源。 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import FieldEditor from './FieldEditor';

afterEach(cleanup);
describe('自然语言别名', () => {
  it('输入新别名后仍绑定原字段ID，说明来源切为用户输入', () => {
    const onChange = vi.fn();
    render(<FieldEditor fields={[{ id: 'score', name: '成绩', type: 'number', aliases: ['分数'], provenance: 'file' }]} onChange={onChange} />);
    const input = screen.getByRole('combobox', { name: '字段别名 成绩' });
    fireEvent.change(input, { target: { value: '得分' } });
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 13, which: 13 });
    expect(onChange).toHaveBeenCalledWith([{ id: 'score', name: '成绩', type: 'number', aliases: ['分数', '得分'], provenance: 'user' }]);
  });
});
