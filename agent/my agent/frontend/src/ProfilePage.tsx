/** 个人主页：维护显示资料、密码和头像，登录账号及数据归属保持稳定。 */
import { useEffect, useState } from 'react';
import { Alert, App, Avatar, Button, Card, Input, Space, Typography } from 'antd';
import { UserOutlined } from '@ant-design/icons';
import { api, errorText, jsonBody } from './api';
import type { User } from './types';

interface Props { user: User; onUpdated: (user: User) => void; onPasswordChanged: () => void }
const avatarLimit = 2 * 1024 * 1024;
export default function ProfilePage({ user, onUpdated, onPasswordChanged }: Props) {
  const { message } = App.useApp();
  const [profile, setProfile] = useState(user); const [displayName, setDisplayName] = useState(user.display_name || ''); const [bio, setBio] = useState(user.bio || ''); const [email, setEmail] = useState(user.email || '');
  const [emailPassword, setEmailPassword] = useState(''); const [currentPassword, setCurrentPassword] = useState(''); const [newPassword, setNewPassword] = useState(''); const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState(''); const [passwordError, setPasswordError] = useState(''); const [avatarError, setAvatarError] = useState(''); const [busy, setBusy] = useState(false); const [passwordBusy, setPasswordBusy] = useState(false); const [avatarBusy, setAvatarBusy] = useState(false);
  useEffect(() => {
    let stopped = false;
    void api<User>('/profile').then(result => { if (!stopped) { setProfile(result); setDisplayName(result.display_name || ''); setBio(result.bio || ''); setEmail(result.email || ''); } }).catch(failure => { if (!stopped) setError(errorText(failure)); });
    return () => { stopped = true; };
  }, [user.id]);
  const emailChanged = (email.trim() || null) !== (profile.email || null);
  const saveProfile = async () => {
    if (emailChanged && !emailPassword) { setError('绑定、修改或解绑邮箱需要验证当前密码。'); return; }
    setBusy(true); setError('');
    try {
      // 只有邮箱变更才发送验证密码；密码仅在内存中短暂保存，成功后立即清空。
      const updated = await api<User>('/profile', { method: 'PATCH', body: jsonBody({ display_name: displayName.trim(), bio, email: email.trim() || null, ...(emailChanged ? { current_password: emailPassword } : {}) }) });
      setProfile(updated); setEmailPassword(''); setDisplayName(updated.display_name || ''); setEmail(updated.email || ''); onUpdated(updated); void message.success('个人资料已保存。');
    } catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); }
  };
  const changePassword = async () => {
    if (newPassword.length < 12) { setPasswordError('新密码至少需要 12 个字符。'); return; }
    if (newPassword !== confirmPassword) { setPasswordError('两次输入的新密码不一致。'); return; }
    setPasswordBusy(true); setPasswordError('');
    try {
      await api('/profile/password', { method: 'POST', body: jsonBody({ current_password: currentPassword, new_password: newPassword }) });
      setCurrentPassword(''); setNewPassword(''); setConfirmPassword(''); void message.success('密码已修改，全部登录会话已撤销，请重新登录。'); onPasswordChanged();
    } catch (failure) { setPasswordError(errorText(failure)); }
    finally { setPasswordBusy(false); }
  };
  const uploadAvatar = async (file: File) => {
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > avatarLimit) { setAvatarError('请选择不超过 2 MB 的 JPG、PNG 或 WebP 图片。'); return; }
    setAvatarBusy(true); setAvatarError('');
    try {
      const body = new FormData(); body.append('file', file);
      const updated = await api<User>('/profile/avatar', { method: 'POST', body }); setProfile(updated); onUpdated(updated); void message.success('头像已更新。');
    } catch (failure) { setAvatarError(errorText(failure)); }
    finally { setAvatarBusy(false); }
  };
  return <section className="profile-page" aria-label="用户个人主页">
    <div className="workbench-heading"><div><h1>用户个人主页</h1><p>修改个人资料、头像及密码。用户名或已绑定邮箱均可用于登录。</p></div></div>
    <Card title="头像"><Space wrap><Avatar size={80} src={profile.avatar_url || undefined} icon={<UserOutlined />} /><div><label className="form-label">上传头像<input type="file" aria-label="上传头像" accept="image/jpeg,image/png,image/webp" disabled={avatarBusy} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void uploadAvatar(file); }} /></label><Typography.Text type="secondary">JPG、PNG 或 WebP，最大 2 MB。</Typography.Text>{avatarBusy && <Typography.Paragraph>正在更新头像…</Typography.Paragraph>}</div></Space>{avatarError && <Alert className="block-gap" type="error" message="头像更新未完成" description={avatarError} />}</Card>
    <Card className="block-gap" title="基础资料"><form onSubmit={event => { event.preventDefault(); void saveProfile(); }}>
      <label className="form-label">登录用户名<Input aria-label="登录用户名" value={profile.username} disabled /></label>
      <label className="form-label">显示昵称<Input aria-label="显示昵称" value={displayName} maxLength={80} onChange={event => setDisplayName(event.target.value)} /></label>
      <label className="form-label">个人简介<Input.TextArea aria-label="个人简介" value={bio} maxLength={1000} onChange={event => setBio(event.target.value)} rows={3} /></label>
      <label className="form-label">绑定邮箱<Input aria-label="绑定邮箱" type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} /></label>
      <Typography.Paragraph type="secondary">邮箱作为唯一登录别名。绑定、修改或解绑需验证当前密码。</Typography.Paragraph>
      {emailChanged && <label className="form-label">邮箱变更验证密码<Input.Password aria-label="邮箱变更验证密码" autoComplete="current-password" value={emailPassword} onChange={event => setEmailPassword(event.target.value)} /></label>}
      {error && <Alert className="block-gap" type="error" message="资料保存未完成" description={error} />}
      <Button className="block-gap" type="primary" htmlType="submit" loading={busy}>保存个人资料</Button>
    </form></Card>
    <Card className="block-gap" title="修改密码"><form onSubmit={event => { event.preventDefault(); void changePassword(); }}>
      <Typography.Paragraph type="secondary">修改成功后，全部登录会话将撤销，需要重新登录。</Typography.Paragraph>
      <label className="form-label">当前密码<Input.Password aria-label="修改密码的当前密码" autoComplete="current-password" required value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} /></label>
      <label className="form-label">新密码<Input.Password aria-label="新密码" autoComplete="new-password" required value={newPassword} onChange={event => setNewPassword(event.target.value)} /></label>
      <label className="form-label">确认新密码<Input.Password aria-label="确认新密码" autoComplete="new-password" required value={confirmPassword} onChange={event => setConfirmPassword(event.target.value)} /></label>
      {passwordError && <Alert type="error" message="密码修改未完成" description={passwordError} />}
      <Button className="block-gap" type="primary" htmlType="submit" loading={passwordBusy}>修改密码并重新登录</Button>
    </form></Card>
  </section>;
}
